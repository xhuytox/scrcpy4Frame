package io.github.xhuytox.scrcpy4frame.companion

import android.app.AlarmManager
import android.app.Notification
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.service.notification.NotificationListenerService
import android.service.notification.NotificationListenerService.Ranking
import android.service.notification.NotificationListenerService.RankingMap
import android.service.notification.StatusBarNotification
import android.util.Log
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException

/**
 * While the listener is connected, owns the [FrameServer], the [WakeServer] and the Frame session:
 * Wireless debugging goes on while the scrcpy4Frame app is around (see [FramePresence]) and off
 * once it has left. Lifecycle callbacks run on the main thread.
 */
class NotifListener : NotificationListenerService() {

    private val mainHandler = Handler(Looper.getMainLooper())
    private val appMeta by lazy { AppMetaResolver(this) }

    @Volatile private var listenerConnected = false
    private var session: Session? = null
    private var networkCallback: ConnectivityManager.NetworkCallback? = null
    private var screenOnReceiver: BroadcastReceiver? = null

    private val presence = FramePresence()
    @Volatile private var wakeServer: WakeServer? = null
    private var sessionCheckReceiver: BroadcastReceiver? = null
    private var sessionCheck: PendingIntent? = null

    private val wifiNetworks: MutableSet<Network> = ConcurrentHashMap.newKeySet()

    /**
     * Untrusted networks where Android switched Wireless debugging straight back off. Wakes and
     * screen-on skip them until Wi-Fi reconnects so Android's prompt doesn't keep reappearing.
     */
    private val declinedNetworks: MutableSet<Network> = ConcurrentHashMap.newKeySet()

    override fun onListenerConnected() {
        super.onListenerConnected()
        Log.i(TAG, "listener connected")
        listenerConnected = true
        startComponents()
    }

    override fun onListenerDisconnected() {
        Log.i(TAG, "listener disconnected")
        listenerConnected = false
        stopComponents()
        super.onListenerDisconnected()
        // Ignored by the system if notification access was revoked.
        try {
            requestRebind(ComponentName(this, NotifListener::class.java))
        } catch (e: RuntimeException) {
            Log.w(TAG, "requestRebind failed", e)
        }
    }

    override fun onDestroy() {
        listenerConnected = false
        stopComponents()
        super.onDestroy()
    }

    private fun startComponents() {
        if (session == null) {
            session = Session().also { it.start() }
        }
        registerWifiCallback()
        registerScreenOnReceiver()
        startFrameSessions()
    }

    private fun stopComponents() {
        stopFrameSessions()
        unregisterScreenOnReceiver()
        unregisterWifiCallback()
        session?.stop()
        session = null
        mainHandler.removeCallbacksAndMessages(null)
        appMeta.clear()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification, rankingMap: RankingMap?) {
        val s = session ?: return
        // Skip the icon work when nobody is listening.
        if (!s.server.hasClients) return
        val snapshot = try {
            snapshotOf(sbn, rankingMap)
        } catch (e: RuntimeException) {
            Log.w(TAG, "could not read notification ${sbn.key}", e)
            return
        }
        s.onPosted(snapshot, sbn.notification?.extras)
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification, rankingMap: RankingMap?) {
        session?.onRemoved(sbn.key)
    }

    private fun snapshotOf(sbn: StatusBarNotification, rankingMap: RankingMap?): NotificationSnapshot {
        val n = sbn.notification
        val extras = n.extras
        val importance = rankingMap?.let { map ->
            val ranking = Ranking()
            if (map.getRanking(sbn.key, ranking)) ranking.importance else null
        }
        return NotificationSnapshot(
            key = sbn.key,
            packageName = sbn.packageName,
            flags = n.flags,
            importance = importance,
            title = extras?.getCharSequence(Notification.EXTRA_TITLE),
            conversationTitle = extras?.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE),
            bigText = extras?.getCharSequence(Notification.EXTRA_BIG_TEXT),
            text = extras?.getCharSequence(Notification.EXTRA_TEXT),
            textLines = extras?.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)?.toList(),
            whenMillis = n.`when`,
            postTimeMillis = sbn.postTime,
        )
    }

    /** One socket server plus the worker that turns notifications into protocol lines. */
    private inner class Session {
        val server = FrameServer(helloProvider = ::hello, onCommand = ::handleCommand)

        private val worker = Executors.newSingleThreadExecutor { r ->
            Thread(r, "s4f-notifications").apply { isDaemon = true }
        }

        /** Keys sent as `posted`, so `removed` only goes out for those. Worker thread only. */
        private val forwardedKeys = object : LinkedHashMap<String, Boolean>() {
            override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Boolean>?): Boolean =
                size > MAX_TRACKED_KEYS
        }

        fun start() = server.start()

        fun stop() {
            server.stop()
            worker.shutdownNow()
        }

        fun onPosted(snapshot: NotificationSnapshot, extras: Bundle?) = runOnWorker {
            if (NotificationRules.skipReason(snapshot, packageName) != null) {
                // A forwarded notification was updated into one we skip, such as an ongoing one.
                if (forwardedKeys.remove(snapshot.key) != null) server.broadcast(Protocol.removed(snapshot.key))
                return@runOnWorker
            }
            val meta = appMeta.resolve(snapshot.packageName, extras)
            val forwarded = NotificationRules.toForwarded(snapshot, meta.label, meta.iconBase64)
            forwardedKeys[snapshot.key] = true
            server.broadcast(Protocol.posted(forwarded))
        }

        fun onRemoved(key: String) = runOnWorker {
            if (forwardedKeys.remove(key) != null) server.broadcast(Protocol.removed(key))
        }

        private fun runOnWorker(task: () -> Unit) {
            try {
                worker.execute {
                    try {
                        task()
                    } catch (e: Exception) {
                        Log.e(TAG, "notification task failed", e)
                    }
                }
            } catch (_: RejectedExecutionException) {
                // Session is shutting down.
            }
        }
    }

    private fun hello(): String = Protocol.hello(
        versionName = BuildConfig.VERSION_NAME,
        versionCode = BuildConfig.VERSION_CODE,
        sdk = Build.VERSION.SDK_INT,
        listenerConnected = listenerConnected,
        autoWirelessDebugging = Prefs.autoWirelessDebugging(this),
        canWriteSecureSettings = WirelessDebugging.canWriteSecureSettings(this),
        // Even before the port is bound: the server keeps retrying, and the Frame stores the port.
        wakePort = if (wakeServer != null) Protocol.WAKE_PORT else 0,
    )

    /** Runs on a client reader thread. */
    private fun handleCommand(clientId: Int, command: Protocol.Command): String? {
        if (presence.heard(clientId, SystemClock.elapsedRealtime())) {
            mainHandler.post { updateFrameSession("frame-connected") }
        }
        return when (command) {
            Protocol.Command.Ping -> Protocol.pong()
            is Protocol.Command.Dismiss -> dismiss(command.key)
            is Protocol.Command.Config -> {
                Prefs.setAutoWirelessDebugging(this, command.autoWirelessDebugging)
                null
            }
            is Protocol.Command.WakeKey -> {
                Prefs.addWakeKey(this, command.key)
                null
            }
            Protocol.Command.Bye -> {
                presence.bye(clientId)
                mainHandler.post { updateFrameSession("frame-bye") }
                null
            }
            is Protocol.Command.Invalid -> Protocol.error(command.message)
        }
    }

    private fun dismiss(key: String): String? {
        if (!listenerConnected) return Protocol.error("dismiss: notification listener is not connected")
        return try {
            cancelNotification(key)
            null
        } catch (e: RuntimeException) {
            Protocol.error("dismiss failed: ${e.message ?: e.javaClass.simpleName}")
        }
    }

    private fun registerWifiCallback() {
        if (networkCallback != null || !WirelessDebugging.isSupported) return
        val cm = getSystemService(ConnectivityManager::class.java) ?: return
        val request = NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            // Also match Wi-Fi without internet access, such as a travel router.
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build()
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                wifiNetworks.add(network)
                declinedNetworks.remove(network)
                tryEnableWirelessDebugging("wifi-available", network)
            }

            override fun onLost(network: Network) {
                wifiNetworks.remove(network)
                declinedNetworks.remove(network)
            }
        }
        try {
            cm.registerNetworkCallback(request, callback)
            networkCallback = callback
        } catch (e: RuntimeException) {
            Log.w(TAG, "registerNetworkCallback failed", e)
        }
    }

    private fun unregisterWifiCallback() {
        val callback = networkCallback ?: return
        networkCallback = null
        try {
            getSystemService(ConnectivityManager::class.java)?.unregisterNetworkCallback(callback)
        } catch (e: RuntimeException) {
            Log.w(TAG, "unregisterNetworkCallback failed", e)
        }
        wifiNetworks.clear()
        declinedNetworks.clear()
    }

    private fun registerScreenOnReceiver() {
        if (screenOnReceiver != null || !WirelessDebugging.isSupported) return
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                if (intent.action != Intent.ACTION_SCREEN_ON) return
                val network = wifiNetworks.firstOrNull { it !in declinedNetworks } ?: return
                tryEnableWirelessDebugging("screen-on", network)
            }
        }
        // Protected system broadcast, so no RECEIVER_EXPORTED/NOT_EXPORTED flag is needed.
        registerReceiver(receiver, IntentFilter(Intent.ACTION_SCREEN_ON))
        screenOnReceiver = receiver
    }

    private fun unregisterScreenOnReceiver() {
        val receiver = screenOnReceiver ?: return
        screenOnReceiver = null
        try {
            unregisterReceiver(receiver)
        } catch (e: IllegalArgumentException) {
            // Not registered.
        }
    }

    /** Called from the connectivity thread or the main thread. Only while a Frame is around. */
    private fun tryEnableWirelessDebugging(trigger: String, network: Network) {
        if (network in declinedNetworks || !frameIsPresent()) return
        if (!WirelessDebugging.maybeEnable(applicationContext, trigger)) return
        mainHandler.postDelayed({
            // Not if the session ended meanwhile: then the companion switched it off itself.
            if (network in wifiNetworks && frameIsPresent() && WirelessDebugging.isEnabled(this) == false) {
                Log.i(TAG, "Wireless debugging was switched back off on this Wi-Fi; not retrying until it reconnects")
                declinedNetworks.add(network)
            }
        }, VERIFY_DELAY_MS)
    }

    private fun frameIsPresent(): Boolean = presence.isPresent(SystemClock.elapsedRealtime())

    private fun startFrameSessions() {
        if (sessionCheckReceiver != null || !WirelessDebugging.isSupported) return
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) = updateFrameSession("frame-timeout")
        }
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) RECEIVER_NOT_EXPORTED else 0
        registerReceiver(receiver, IntentFilter(ACTION_SESSION_CHECK), flags)
        sessionCheckReceiver = receiver
        sessionCheck = PendingIntent.getBroadcast(
            this,
            0,
            Intent(ACTION_SESSION_CHECK).setPackage(packageName),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        wakeServer = WakeServer(keys = ::wakeKeys, onWake = ::onWake).also { it.start() }
        // The process stopped during a session. Give the Frame time to reconnect before ending it.
        if (Prefs.frameSession(this)) presence.woken(SystemClock.elapsedRealtime())
        updateFrameSession("start")
    }

    private fun stopFrameSessions() {
        wakeServer?.stop()
        wakeServer = null
        sessionCheck?.let { getSystemService(AlarmManager::class.java)?.cancel(it) }
        sessionCheck = null
        val receiver = sessionCheckReceiver ?: return
        sessionCheckReceiver = null
        try {
            unregisterReceiver(receiver)
        } catch (e: IllegalArgumentException) {
            // Not registered.
        }
    }

    /** Runs on the wake thread. Nothing answers a wake while the companion couldn't act on it. */
    private fun wakeKeys(): List<ByteArray> =
        if (Prefs.autoWirelessDebugging(this) && WirelessDebugging.canWriteSecureSettings(this)) {
            Prefs.wakeKeys(this)
        } else {
            emptyList()
        }

    /** Runs on the wake thread. */
    private fun onWake() {
        presence.woken(SystemClock.elapsedRealtime())
        mainHandler.post {
            updateFrameSession("wake")
            val network = wifiNetworks.firstOrNull { it !in declinedNetworks } ?: return@post
            tryEnableWirelessDebugging("wake", network)
        }
    }

    /**
     * Main thread. Starts the Frame session when a Frame shows up and ends it, turning Wireless
     * debugging off, once none is left. While one is around, an alarm checks again when it would
     * time out. The alarm runs in Doze too, so a Frame that went away without `bye` (crashed, slept
     * or left the Wi-Fi) still ends the session about [FramePresence.TIMEOUT_MS] later.
     */
    private fun updateFrameSession(trigger: String) {
        val check = sessionCheck ?: return
        val alarms = getSystemService(AlarmManager::class.java) ?: return
        val expiresAt = presence.expiresAt(SystemClock.elapsedRealtime())
        if (expiresAt != null) {
            if (!Prefs.frameSession(this)) {
                Log.i(TAG, "$trigger: a Frame is here")
                Prefs.setFrameSession(this, true)
            }
            alarms.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, expiresAt, check)
            return
        }
        alarms.cancel(check)
        if (Prefs.frameSession(this)) {
            Log.i(TAG, "$trigger: no Frame left; turning Wireless debugging off")
            Prefs.setFrameSession(this, false)
            WirelessDebugging.maybeDisable(applicationContext, trigger)
        }
    }

    private companion object {
        const val TAG = "S4F.NotifListener"
        const val MAX_TRACKED_KEYS = 1_000
        const val VERIFY_DELAY_MS = 5_000L
        const val ACTION_SESSION_CHECK = "io.github.xhuytox.scrcpy4frame.companion.SESSION_CHECK"
    }
}

import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "io.github.xhuytox.scrcpy4frame.companion"
    compileSdk = 36

    defaultConfig {
        applicationId = "io.github.xhuytox.scrcpy4frame.companion"
        minSdk = 26
        targetSdk = 36
        // Reported to the Frame in the protocol "hello" message.
        versionCode = 2
        versionName = "1.0.1"
    }

    buildFeatures {
        buildConfig = true
        viewBinding = true
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            isShrinkResources = false
            // Signed with the debug key so assembleRelease needs no keystore. The APK is only ever
            // sideloaded by the Frame. Debug keys differ per machine, so the Frame uninstalls and
            // reinstalls on INSTALL_FAILED_UPDATE_INCOMPATIBLE.
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    // android.jar stubs return 0/null instead of throwing, so JVM tests can run code that logs
    // or reads SystemClock (WakeServer).
    testOptions {
        unitTests.isReturnDefaultValues = true
    }

    dependenciesInfo {
        // Not a Play build, so skip the dependency-metadata block.
        includeInApk = false
        includeInBundle = false
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
    // android.jar's org.json is stubs that throw, so JVM tests need the real one.
    testImplementation("org.json:json:20260814")
}

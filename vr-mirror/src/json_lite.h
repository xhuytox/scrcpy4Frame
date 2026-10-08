// Minimal JSON for the line protocol: one flat object per line. Nested values are validated
// and skipped, same as vr-toast's parser.
#pragma once

#include <cctype>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace vrm::json {

struct Value {
  enum class Type { String, Number, Bool, Null, Composite };
  Type type = Type::Null;
  std::string str;
  double num = 0.0;
  bool boolean = false;
};

using Object = std::vector<std::pair<std::string, Value>>;

class Reader {
 public:
  explicit Reader(std::string_view s) : s_(s) {}

  bool parse_object(Object& out) {
    out.clear();
    skip_ws();
    if (!eat('{')) return false;
    skip_ws();
    if (eat('}')) return at_end();
    for (;;) {
      std::string key;
      skip_ws();
      if (!parse_string(&key)) return false;
      skip_ws();
      if (!eat(':')) return false;
      skip_ws();
      Value value;
      if (!parse_value(value, 0)) return false;
      out.emplace_back(std::move(key), std::move(value));
      skip_ws();
      if (eat(',')) continue;
      if (eat('}')) return at_end();
      return false;
    }
  }

 private:
  static constexpr int kMaxDepth = 32;

  bool at_end() {
    skip_ws();
    return i_ == s_.size();
  }

  void skip_ws() {
    while (i_ < s_.size() && (s_[i_] == ' ' || s_[i_] == '\t' || s_[i_] == '\n' || s_[i_] == '\r')) {
      ++i_;
    }
  }

  bool eat(char c) {
    if (i_ < s_.size() && s_[i_] == c) {
      ++i_;
      return true;
    }
    return false;
  }

  bool eat_word(const char* w) {
    const size_t n = std::strlen(w);
    if (s_.substr(i_, n) == w) {
      i_ += n;
      return true;
    }
    return false;
  }

  static int hex_value(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
  }

  bool parse_hex4(uint32_t* out) {
    if (i_ + 4 > s_.size()) return false;
    uint32_t v = 0;
    for (int k = 0; k < 4; ++k) {
      const int h = hex_value(s_[i_ + k]);
      if (h < 0) return false;
      v = (v << 4) | static_cast<uint32_t>(h);
    }
    i_ += 4;
    *out = v;
    return true;
  }

  static void append_utf8(std::string* out, uint32_t cp) {
    if (cp < 0x80) {
      *out += static_cast<char>(cp);
    } else if (cp < 0x800) {
      *out += static_cast<char>(0xC0 | (cp >> 6));
      *out += static_cast<char>(0x80 | (cp & 0x3F));
    } else if (cp < 0x10000) {
      *out += static_cast<char>(0xE0 | (cp >> 12));
      *out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
      *out += static_cast<char>(0x80 | (cp & 0x3F));
    } else {
      *out += static_cast<char>(0xF0 | (cp >> 18));
      *out += static_cast<char>(0x80 | ((cp >> 12) & 0x3F));
      *out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
      *out += static_cast<char>(0x80 | (cp & 0x3F));
    }
  }

  // With out == nullptr the string is only validated.
  bool parse_string(std::string* out) {
    if (!eat('"')) return false;
    for (;;) {
      const size_t start = i_;
      while (i_ < s_.size() && s_[i_] != '"' && s_[i_] != '\\' &&
             static_cast<unsigned char>(s_[i_]) >= 0x20) {
        ++i_;
      }
      if (out) out->append(s_.data() + start, i_ - start);
      if (i_ >= s_.size()) return false;
      const char c = s_[i_++];
      if (c == '"') return true;
      if (c != '\\') return false;  // raw control character
      if (i_ >= s_.size()) return false;
      const char e = s_[i_++];
      char decoded = 0;
      switch (e) {
        case '"': decoded = '"'; break;
        case '\\': decoded = '\\'; break;
        case '/': decoded = '/'; break;
        case 'b': decoded = '\b'; break;
        case 'f': decoded = '\f'; break;
        case 'n': decoded = '\n'; break;
        case 'r': decoded = '\r'; break;
        case 't': decoded = '\t'; break;
        case 'u': {
          uint32_t cp = 0;
          if (!parse_hex4(&cp)) return false;
          if (cp >= 0xD800 && cp <= 0xDBFF) {
            uint32_t lo = 0;
            if (!eat('\\') || !eat('u') || !parse_hex4(&lo) || lo < 0xDC00 || lo > 0xDFFF) {
              return false;
            }
            cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
          } else if (cp >= 0xDC00 && cp <= 0xDFFF) {
            return false;
          }
          if (out) append_utf8(out, cp);
          continue;
        }
        default:
          return false;
      }
      if (out) *out += decoded;
    }
  }

  bool parse_number(double* out) {
    const size_t start = i_;
    if (i_ < s_.size() && s_[i_] == '-') ++i_;
    while (i_ < s_.size() && (std::isdigit(static_cast<unsigned char>(s_[i_])) || s_[i_] == '.' ||
                              s_[i_] == 'e' || s_[i_] == 'E' || s_[i_] == '+' || s_[i_] == '-')) {
      ++i_;
    }
    if (i_ == start) return false;
    const std::string token(s_.substr(start, i_ - start));
    char* end = nullptr;
    errno = 0;
    const double v = std::strtod(token.c_str(), &end);
    if (end != token.c_str() + token.size() || errno == ERANGE || !std::isfinite(v)) return false;
    *out = v;
    return true;
  }

  bool parse_value(Value& v, int depth) {
    if (i_ >= s_.size()) return false;
    const char c = s_[i_];
    if (c == '"') {
      v.type = Value::Type::String;
      return parse_string(depth == 0 ? &v.str : nullptr);
    }
    if (c == '{' || c == '[') {
      v.type = Value::Type::Composite;
      return skip_composite(depth + 1);
    }
    if (eat_word("true")) {
      v.type = Value::Type::Bool;
      v.boolean = true;
      return true;
    }
    if (eat_word("false")) {
      v.type = Value::Type::Bool;
      v.boolean = false;
      return true;
    }
    if (eat_word("null")) {
      v.type = Value::Type::Null;
      return true;
    }
    v.type = Value::Type::Number;
    return parse_number(&v.num);
  }

  bool skip_composite(int depth) {
    if (depth > kMaxDepth) return false;
    const char open = s_[i_++];
    const char close = open == '{' ? '}' : ']';
    skip_ws();
    if (eat(close)) return true;
    for (;;) {
      skip_ws();
      if (open == '{') {
        if (!parse_string(nullptr)) return false;
        skip_ws();
        if (!eat(':')) return false;
        skip_ws();
      }
      Value ignored;
      if (!parse_value(ignored, depth)) return false;
      skip_ws();
      if (eat(',')) continue;
      return eat(close);
    }
  }

  std::string_view s_;
  size_t i_ = 0;
};

inline const Value* find(const Object& obj, std::string_view key) {
  for (auto it = obj.rbegin(); it != obj.rend(); ++it) {  // last occurrence wins
    if (it->first == key) return &it->second;
  }
  return nullptr;
}

inline const std::string* string_field(const Object& obj, std::string_view key) {
  const Value* v = find(obj, key);
  return (v && v->type == Value::Type::String) ? &v->str : nullptr;
}

inline bool number_field(const Object& obj, std::string_view key, double* out) {
  const Value* v = find(obj, key);
  if (!v || v->type != Value::Type::Number) return false;
  *out = v->num;
  return true;
}

inline bool integer_field(const Object& obj, std::string_view key, long* out) {
  const Value* v = find(obj, key);
  if (!v || v->type != Value::Type::Number) return false;
  if (v->num != std::floor(v->num) || std::fabs(v->num) > 9.0e15) return false;
  *out = static_cast<long>(v->num);
  return true;
}

inline bool bool_field(const Object& obj, std::string_view key, bool* out) {
  const Value* v = find(obj, key);
  if (!v || v->type != Value::Type::Bool) return false;
  *out = v->boolean;
  return true;
}

// Replaces invalid UTF-8 with U+FFFD. Device names come straight from the phone.
inline void append_string(std::string& out, std::string_view s) {
  out += '"';
  size_t i = 0;
  while (i < s.size()) {
    const unsigned char c = static_cast<unsigned char>(s[i]);
    if (c < 0x80) {
      switch (c) {
        case '"': out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        case '\b': out += "\\b"; break;
        case '\f': out += "\\f"; break;
        default:
          if (c < 0x20 || c == 0x7F) {
            char buf[8];
            std::snprintf(buf, sizeof buf, "\\u%04x", c);
            out += buf;
          } else {
            out += static_cast<char>(c);
          }
      }
      ++i;
      continue;
    }
    // Reject bad lead/continuation bytes, overlongs and surrogates.
    size_t len = 0;
    uint32_t cp = 0;
    if (c >= 0xC2 && c <= 0xDF) {
      len = 2;
      cp = c & 0x1F;
    } else if (c >= 0xE0 && c <= 0xEF) {
      len = 3;
      cp = c & 0x0F;
    } else if (c >= 0xF0 && c <= 0xF4) {
      len = 4;
      cp = c & 0x07;
    }
    bool ok = len != 0 && i + len <= s.size();
    for (size_t k = 1; ok && k < len; ++k) {
      const unsigned char cc = static_cast<unsigned char>(s[i + k]);
      if ((cc & 0xC0) != 0x80) {
        ok = false;
      } else {
        cp = (cp << 6) | (cc & 0x3F);
      }
    }
    if (ok) {
      if ((len == 3 && cp < 0x800) || (len == 4 && (cp < 0x10000 || cp > 0x10FFFF)) ||
          (cp >= 0xD800 && cp <= 0xDFFF)) {
        ok = false;
      }
    }
    if (ok) {
      out.append(s.data() + i, len);
      i += len;
    } else {
      out += "\xEF\xBF\xBD";  // U+FFFD
      ++i;
    }
  }
  out += '"';
}

// Builds one flat JSON object: Writer().str("event","size").integer("width",864).finish()
class Writer {
 public:
  Writer& str(std::string_view key, std::string_view value) {
    begin_key(key);
    append_string(s_, value);
    return *this;
  }
  Writer& integer(std::string_view key, long long value) {
    begin_key(key);
    s_ += std::to_string(value);
    return *this;
  }
  Writer& number(std::string_view key, double value, int decimals) {
    begin_key(key);
    if (!std::isfinite(value)) value = 0.0;
    char buf[64];
    std::snprintf(buf, sizeof buf, "%.*f", decimals, value);
    s_ += buf;
    return *this;
  }
  Writer& boolean(std::string_view key, bool value) {
    begin_key(key);
    s_ += value ? "true" : "false";
    return *this;
  }
  std::string finish() const { return s_ + "}"; }

 private:
  void begin_key(std::string_view key) {
    s_ += first_ ? "" : ",";
    first_ = false;
    append_string(s_, key);
    s_ += ':';
  }

  std::string s_ = "{";
  bool first_ = true;
};

}  // namespace vrm::json

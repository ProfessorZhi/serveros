// A minimal, strict JSON reader, private to this component.
//
// It exists because `nodecore::Json` writes and never reads — the collector
// never had to consume a document — and two things here do: the desired-state
// manifest a person writes, and the recovery state this program writes for
// itself. One reader serves both rather than two parsers free to disagree
// about what a number or a duplicate key is.
//
// It is in `src/`, not `include/`: nothing outside this component gets to
// depend on it, so it can change without a contract review.
//
// What "strict" buys, each a real refusal rather than a style:
//   * a duplicate key is refused (RFC 8259 leaves it undefined, and a reader
//     that picks one silently picks whichever its container happens to keep);
//   * a control character in a string must be escaped, so a newline cannot
//     turn a one-line field into two;
//   * a leading zero is not a number, so `01` cannot arrive as `1`;
//   * nesting is capped, because a recursive-descent reader is a stack
//     overflow waiting for a document from somewhere untrusted.
#pragma once

#include <cctype>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

namespace nodecore::supervisor::detail {

struct Json {
    enum class Kind { Null, Bool, Int, Real, String, Array, Object };

    Kind kind = Kind::Null;
    bool boolean = false;
    long long integer = 0;
    double real = 0.0;
    std::string text;
    std::vector<Json> items;
    std::vector<std::pair<std::string, Json>> members;

    bool is_object() const { return kind == Kind::Object; }
    bool is_array() const { return kind == Kind::Array; }
    bool is_string() const { return kind == Kind::String; }
    bool is_bool() const { return kind == Kind::Bool; }
    bool is_int() const { return kind == Kind::Int; }

    const Json* find(const std::string& key) const {
        for (const auto& member : members) {
            if (member.first == key) {
                return &member.second;
            }
        }
        return nullptr;
    }
};

class Reader {
public:
    explicit Reader(const std::string& text) : text_(text) {}

    bool parse(Json& out, std::string& error) {
        skip();
        if (!value(out, error, 0)) {
            return false;
        }
        skip();
        if (pos_ != text_.size()) {
            error = "trailing bytes after the document";
            return false;
        }
        return true;
    }

private:
    const std::string& text_;
    std::size_t pos_ = 0;

    // Every structural error carries the byte it happened at. "expected ',' or
    // '}'" alone sends a reader to re-read their whole document; with the
    // offset and the byte found, a real reader bug was located in one line of
    // output instead of one afternoon.
    std::string where(const std::string& what) const {
        std::string message = what + " at byte " + std::to_string(pos_);
        if (pos_ < text_.size()) {
            message += " (found '";
            message += text_[pos_];
            message += "')";
        } else {
            message += " (end of document)";
        }
        return message;
    }

    void skip() {
        while (pos_ < text_.size()) {
            const char c = text_[pos_];
            if (c != ' ' && c != '\t' && c != '\n' && c != '\r') {
                return;
            }
            ++pos_;
        }
    }

    bool literal(const char* word) {
        const std::size_t n = std::strlen(word);
        if (text_.compare(pos_, n, word) != 0) {
            return false;
        }
        pos_ += n;
        return true;
    }

    static void append_utf8(std::string& out, unsigned codepoint) {
        if (codepoint <= 0x7fu) {
            out.push_back(static_cast<char>(codepoint));
        } else if (codepoint <= 0x7ffu) {
            out.push_back(static_cast<char>(0xc0u | (codepoint >> 6)));
            out.push_back(static_cast<char>(0x80u | (codepoint & 0x3fu)));
        } else if (codepoint <= 0xffffu) {
            out.push_back(static_cast<char>(0xe0u | (codepoint >> 12)));
            out.push_back(static_cast<char>(0x80u | ((codepoint >> 6) & 0x3fu)));
            out.push_back(static_cast<char>(0x80u | (codepoint & 0x3fu)));
        } else {
            out.push_back(static_cast<char>(0xf0u | (codepoint >> 18)));
            out.push_back(static_cast<char>(0x80u | ((codepoint >> 12) & 0x3fu)));
            out.push_back(static_cast<char>(0x80u | ((codepoint >> 6) & 0x3fu)));
            out.push_back(static_cast<char>(0x80u | (codepoint & 0x3fu)));
        }
    }

    bool hex4(unsigned& out) {
        if (pos_ + 4 > text_.size()) {
            return false;
        }
        unsigned value = 0;
        for (int i = 0; i < 4; ++i) {
            const char c = text_[pos_ + static_cast<std::size_t>(i)];
            unsigned digit = 0;
            if (c >= '0' && c <= '9') {
                digit = static_cast<unsigned>(c - '0');
            } else if (c >= 'a' && c <= 'f') {
                digit = static_cast<unsigned>(c - 'a') + 10u;
            } else if (c >= 'A' && c <= 'F') {
                digit = static_cast<unsigned>(c - 'A') + 10u;
            } else {
                return false;
            }
            value = value * 16u + digit;
        }
        pos_ += 4;
        out = value;
        return true;
    }

    bool string_literal(std::string& out, std::string& error) {
        if (pos_ >= text_.size() || text_[pos_] != '"') {
            error = where("expected a string");
            return false;
        }
        ++pos_;
        out.clear();
        while (true) {
            if (pos_ >= text_.size()) {
                error = "unterminated string";
                return false;
            }
            const unsigned char c = static_cast<unsigned char>(text_[pos_]);
            if (c == '"') {
                ++pos_;
                return true;
            }
            if (c == '\\') {
                ++pos_;
                if (pos_ >= text_.size()) {
                    error = "unterminated escape";
                    return false;
                }
                const char escape = text_[pos_++];
                switch (escape) {
                    case '"': out.push_back('"'); break;
                    case '\\': out.push_back('\\'); break;
                    case '/': out.push_back('/'); break;
                    case 'b': out.push_back('\b'); break;
                    case 'f': out.push_back('\f'); break;
                    case 'n': out.push_back('\n'); break;
                    case 'r': out.push_back('\r'); break;
                    case 't': out.push_back('\t'); break;
                    case 'u': {
                        unsigned codepoint = 0;
                        if (!hex4(codepoint)) {
                            error = "a \\u escape needs four hex digits";
                            return false;
                        }
                        if (codepoint >= 0xD800u && codepoint <= 0xDBFFu) {
                            if (pos_ + 2 > text_.size() || text_[pos_] != '\\' ||
                                text_[pos_ + 1] != 'u') {
                                error = "a high surrogate must be followed by a low surrogate";
                                return false;
                            }
                            pos_ += 2;
                            unsigned low = 0;
                            if (!hex4(low)) {
                                error = "a \\u escape needs four hex digits";
                                return false;
                            }
                            if (low < 0xDC00u || low > 0xDFFFu) {
                                error = "a high surrogate must be followed by a low surrogate";
                                return false;
                            }
                            codepoint = 0x10000u + ((codepoint - 0xD800u) << 10) + (low - 0xDC00u);
                        } else if (codepoint >= 0xDC00u && codepoint <= 0xDFFFu) {
                            error = "a low surrogate appeared on its own";
                            return false;
                        }
                        append_utf8(out, codepoint);
                        break;
                    }
                    default:
                        error = std::string("unknown escape \\") + escape;
                        return false;
                }
                continue;
            }
            if (c < 0x20u) {
                error = "a control character must be escaped";
                return false;
            }
            out.push_back(static_cast<char>(c));
            ++pos_;
        }
    }

    bool number(Json& out, std::string& error) {
        const std::size_t start = pos_;
        if (pos_ < text_.size() && text_[pos_] == '-') {
            ++pos_;
        }
        if (pos_ >= text_.size() || !std::isdigit(static_cast<unsigned char>(text_[pos_]))) {
            error = where("expected a digit in a number");
            return false;
        }
        if (text_[pos_] == '0') {
            ++pos_;
            if (pos_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[pos_]))) {
                error = where("a number may not have a leading zero");
                return false;
            }
        } else {
            while (pos_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[pos_]))) {
                ++pos_;
            }
        }
        bool is_real = false;
        if (pos_ < text_.size() && text_[pos_] == '.') {
            is_real = true;
            ++pos_;
            if (pos_ >= text_.size() || !std::isdigit(static_cast<unsigned char>(text_[pos_]))) {
                error = where("expected a digit after the decimal point");
                return false;
            }
            while (pos_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[pos_]))) {
                ++pos_;
            }
        }
        if (pos_ < text_.size() && (text_[pos_] == 'e' || text_[pos_] == 'E')) {
            is_real = true;
            ++pos_;
            if (pos_ < text_.size() && (text_[pos_] == '+' || text_[pos_] == '-')) {
                ++pos_;
            }
            if (pos_ >= text_.size() || !std::isdigit(static_cast<unsigned char>(text_[pos_]))) {
                error = where("expected a digit in the exponent");
                return false;
            }
            while (pos_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[pos_]))) {
                ++pos_;
            }
        }
        const std::string token = text_.substr(start, pos_ - start);
        if (is_real) {
            out.kind = Json::Kind::Real;
            out.real = std::strtod(token.c_str(), nullptr);
        } else {
            out.kind = Json::Kind::Int;
            out.integer = std::strtoll(token.c_str(), nullptr, 10);
        }
        return true;
    }

    bool object(Json& out, std::string& error, int depth) {
        ++pos_;
        out.kind = Json::Kind::Object;
        out.members.clear();
        skip();
        if (pos_ < text_.size() && text_[pos_] == '}') {
            ++pos_;
            return true;
        }
        while (true) {
            skip();
            std::string key;
            if (!string_literal(key, error)) {
                return false;
            }
            for (const auto& member : out.members) {
                if (member.first == key) {
                    error = "duplicate key \"" + key + "\"";
                    return false;
                }
            }
            skip();
            if (pos_ >= text_.size() || text_[pos_] != ':') {
                error = where("expected ':' after a key");
                return false;
            }
            ++pos_;
            Json value_json;
            if (!value(value_json, error, depth + 1)) {
                return false;
            }
            out.members.emplace_back(std::move(key), std::move(value_json));
            skip();
            if (pos_ >= text_.size()) {
                error = "unterminated object";
                return false;
            }
            if (text_[pos_] == ',') {
                ++pos_;
                continue;
            }
            if (text_[pos_] == '}') {
                ++pos_;
                return true;
            }
            error = where("expected ',' or '}'");
            return false;
        }
    }

    bool array(Json& out, std::string& error, int depth) {
        ++pos_;
        out.kind = Json::Kind::Array;
        out.items.clear();
        skip();
        if (pos_ < text_.size() && text_[pos_] == ']') {
            ++pos_;
            return true;
        }
        while (true) {
            Json item;
            if (!value(item, error, depth + 1)) {
                return false;
            }
            out.items.push_back(std::move(item));
            skip();
            if (pos_ >= text_.size()) {
                error = "unterminated array";
                return false;
            }
            if (text_[pos_] == ',') {
                ++pos_;
                continue;
            }
            if (text_[pos_] == ']') {
                ++pos_;
                return true;
            }
            error = where("expected ',' or ']'");
            return false;
        }
    }

    bool value(Json& out, std::string& error, int depth) {
        if (depth > 32) {
            error = "document nested deeper than 32 levels";
            return false;
        }
        skip();
        if (pos_ >= text_.size()) {
            error = "unexpected end of document";
            return false;
        }
        const char c = text_[pos_];
        if (c == '{') {
            return object(out, error, depth);
        }
        if (c == '[') {
            return array(out, error, depth);
        }
        if (c == '"') {
            out.kind = Json::Kind::String;
            return string_literal(out.text, error);
        }
        if (c == 't') {
            if (!literal("true")) {
                error = where("expected 'true'");
                return false;
            }
            out.kind = Json::Kind::Bool;
            out.boolean = true;
            return true;
        }
        if (c == 'f') {
            if (!literal("false")) {
                error = where("expected 'false'");
                return false;
            }
            out.kind = Json::Kind::Bool;
            out.boolean = false;
            return true;
        }
        if (c == 'n') {
            if (!literal("null")) {
                error = where("expected 'null'");
                return false;
            }
            out.kind = Json::Kind::Null;
            return true;
        }
        if (c == '-' || std::isdigit(static_cast<unsigned char>(c))) {
            return number(out, error);
        }
        error = where(std::string("unexpected character '") + c + "'");
        return false;
    }
};

}  // namespace nodecore::supervisor::detail

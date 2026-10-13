#include "nodecore/json.hpp"

#include <algorithm>
#include <cstdio>
#include <numeric>

namespace nodecore {
namespace {

void indent(std::string& out, int depth) {
    out.append(static_cast<std::size_t>(depth) * 2, ' ');
}

void append_uinteger(std::string& out, std::uint64_t value) {
    char buffer[32];
    std::snprintf(buffer, sizeof(buffer), "%llu", static_cast<unsigned long long>(value));
    out += buffer;
}

}  // namespace

Json Json::boolean(bool value) {
    Json node;
    node.kind_ = Kind::Bool;
    node.bool_ = value;
    return node;
}

Json Json::integer(std::int64_t value) {
    Json node;
    node.kind_ = Kind::Int;
    node.int_ = value;
    return node;
}

Json Json::uinteger(std::uint64_t value) {
    Json node;
    node.kind_ = Kind::Uint;
    node.uint_ = value;
    return node;
}

Json Json::number(double value, int precision) {
    Json node;
    node.kind_ = Kind::Double;
    node.double_ = value;
    node.precision_ = precision < 0 ? 0 : (precision > 9 ? 9 : precision);
    return node;
}

Json Json::string(std::string value) {
    Json node;
    node.kind_ = Kind::String;
    node.text_ = std::move(value);
    return node;
}

Json Json::array() {
    Json node;
    node.kind_ = Kind::Array;
    return node;
}

Json Json::object() {
    Json node;
    node.kind_ = Kind::Object;
    return node;
}

Json Json::unavailable(std::string reason) {
    Json node = Json::object();
    node.set("state", Json::string("UNAVAILABLE"));
    node.set("reason", Json::string(std::move(reason)));
    return node;
}

Json& Json::push(Json value) {
    items_.push_back(std::move(value));
    return *this;
}

Json& Json::set(std::string key, Json value) {
    for (auto& member : members_) {
        if (member.first == key) {
            member.second = std::move(value);
            return *this;
        }
    }
    members_.emplace_back(std::move(key), std::move(value));
    return *this;
}

bool Json::has(const std::string& key) const {
    for (const auto& member : members_) {
        if (member.first == key) {
            return true;
        }
    }
    return false;
}

std::vector<std::size_t> Json::sorted_member_order() const {
    std::vector<std::size_t> order(members_.size());
    std::iota(order.begin(), order.end(), std::size_t{0});
    std::stable_sort(order.begin(), order.end(), [this](std::size_t left, std::size_t right) {
        return members_[left].first < members_[right].first;
    });
    return order;
}

std::string json_escape(const std::string& raw) {
    std::string out;
    out.reserve(raw.size() + 8);
    for (const char ch : raw) {
        const unsigned char byte = static_cast<unsigned char>(ch);
        switch (ch) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            default:
                if (byte < 0x20) {
                    char buffer[8];
                    std::snprintf(buffer, sizeof(buffer), "\\u%04x", byte);
                    out += buffer;
                } else {
                    out.push_back(ch);
                }
        }
    }
    return out;
}

void Json::dump_into(std::string& out, int depth) const {
    switch (kind_) {
        case Kind::Null:
            out += "null";
            return;
        case Kind::Bool:
            out += bool_ ? "true" : "false";
            return;
        case Kind::Int: {
            char buffer[32];
            std::snprintf(buffer, sizeof(buffer), "%lld", static_cast<long long>(int_));
            out += buffer;
            return;
        }
        case Kind::Uint:
            append_uinteger(out, uint_);
            return;
        case Kind::Double: {
            char buffer[64];
            std::snprintf(buffer, sizeof(buffer), "%.*f", precision_, double_);
            out += buffer;
            return;
        }
        case Kind::String:
            out.push_back('"');
            out += json_escape(text_);
            out.push_back('"');
            return;
        case Kind::Array: {
            if (items_.empty()) {
                out += "[]";
                return;
            }
            out += "[\n";
            for (std::size_t index = 0; index < items_.size(); ++index) {
                indent(out, depth + 1);
                items_[index].dump_into(out, depth + 1);
                if (index + 1 < items_.size()) {
                    out.push_back(',');
                }
                out.push_back('\n');
            }
            indent(out, depth);
            out.push_back(']');
            return;
        }
        case Kind::Object: {
            if (members_.empty()) {
                out += "{}";
                return;
            }
            const std::vector<std::size_t> order = sorted_member_order();
            out += "{\n";
            for (std::size_t position = 0; position < order.size(); ++position) {
                const auto& member = members_[order[position]];
                indent(out, depth + 1);
                out.push_back('"');
                out += json_escape(member.first);
                out += "\": ";
                member.second.dump_into(out, depth + 1);
                if (position + 1 < order.size()) {
                    out.push_back(',');
                }
                out.push_back('\n');
            }
            indent(out, depth);
            out.push_back('}');
            return;
        }
    }
    // Unreachable; every Kind is handled above.
}

void Json::dump_compact_into(std::string& out) const {
    switch (kind_) {
        case Kind::Null:
            out += "null";
            return;
        case Kind::Bool:
            out += bool_ ? "true" : "false";
            return;
        case Kind::Int: {
            char buffer[32];
            std::snprintf(buffer, sizeof(buffer), "%lld", static_cast<long long>(int_));
            out += buffer;
            return;
        }
        case Kind::Uint:
            append_uinteger(out, uint_);
            return;
        case Kind::Double: {
            char buffer[64];
            std::snprintf(buffer, sizeof(buffer), "%.*f", precision_, double_);
            out += buffer;
            return;
        }
        case Kind::String:
            out.push_back('"');
            out += json_escape(text_);
            out.push_back('"');
            return;
        case Kind::Array: {
            out.push_back('[');
            for (std::size_t index = 0; index < items_.size(); ++index) {
                if (index != 0) {
                    out.push_back(',');
                }
                items_[index].dump_compact_into(out);
            }
            out.push_back(']');
            return;
        }
        case Kind::Object: {
            const std::vector<std::size_t> order = sorted_member_order();
            out.push_back('{');
            for (std::size_t position = 0; position < order.size(); ++position) {
                if (position != 0) {
                    out.push_back(',');
                }
                const auto& member = members_[order[position]];
                out.push_back('"');
                out += json_escape(member.first);
                out += "\":";
                member.second.dump_compact_into(out);
            }
            out.push_back('}');
            return;
        }
    }
}

std::string Json::dump() const {
    std::string out;
    dump_into(out, 0);
    return out;
}

std::string Json::dump_compact() const {
    std::string out;
    dump_compact_into(out);
    return out;
}

}  // namespace nodecore

// Deterministic JSON writer for the native node-core collector.
//
// This is not a general JSON library. It writes only, never parses, and it makes
// three choices on purpose:
//
//   * object keys are emitted in lexicographic order. That is not a style
//     preference: the Python core serialises every payload it takes an identity
//     hash over with ``sort_keys=True`` (``runtime/card_delivery.py:232``,
//     ``runtime/alert_digest.py:1029``), and the adapter downstream will compare
//     and hash these snapshots. Matching that order is what makes the comparison
//     possible at all.
//   * every double carries its own precision and is formatted under the classic
//     "C" locale, so a value can never come out as "1,5" on a host whose locale
//     uses a decimal comma.
//   * two dump forms exist, matching the two the Python core already uses: a
//     pretty form for a human reading it, and a compact "canonical" form for
//     hashing, because a reader who re-indents a document must not thereby give
//     the same facts a different identity.
#pragma once

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace nodecore {

class Json {
public:
    enum class Kind { Null, Bool, Int, Uint, Double, String, Array, Object };

    static Json null() { return Json(); }
    static Json boolean(bool value);
    static Json integer(std::int64_t value);
    static Json uinteger(std::uint64_t value);

    // `precision` is the number of digits after the decimal point. It travels
    // with the value rather than being a property of the writer, because one
    // document mixes a percent (1) and a second count (6) and neither should be
    // rounded to the other's habit.
    static Json number(double value, int precision);
    static Json string(std::string value);
    static Json array();
    static Json object();

    // The shape this collector uses for every fact it could not measure:
    // {"state": "UNAVAILABLE", "reason": "..."}. It mirrors
    // ``runtime/inventory.py:93`` ``_unavailable()`` exactly, and it exists so an
    // unmeasured value can never be mistaken for a measured zero.
    static Json unavailable(std::string reason);

    Json& push(Json value);
    Json& set(std::string key, Json value);
    bool has(const std::string& key) const;

    Kind kind() const { return kind_; }
    const std::string& text() const { return text_; }
    std::uint64_t as_uinteger() const { return uint_; }
    const std::vector<Json>& items() const { return items_; }

    // Two-space indent, ": " after a key, no trailing newline.
    std::string dump() const;

    // No whitespace at all. Combined with the sorted keys above this reproduces
    // the Python core's canonical payload bytes.
    std::string dump_compact() const;

private:
    Kind kind_ = Kind::Null;
    bool bool_ = false;
    std::int64_t int_ = 0;
    std::uint64_t uint_ = 0;
    double double_ = 0.0;
    int precision_ = 2;
    std::string text_;
    std::vector<Json> items_;
    std::vector<std::pair<std::string, Json>> members_;

    // Indices of members_ in emission order (lexicographic by key). Computed per
    // dump rather than cached, because a Json is built by value and moved.
    std::vector<std::size_t> sorted_member_order() const;

    void dump_into(std::string& out, int depth) const;
    void dump_compact_into(std::string& out) const;
};

// RFC 8259 string escaping. Bytes outside the escape set pass through as UTF-8,
// matching the ``ensure_ascii=False`` the Python core uses: a mount path or an
// interface name is quoted verbatim, never transliterated.
std::string json_escape(const std::string& raw);

}  // namespace nodecore

#include <string>

#include "check.hpp"
#include "nodecore/json.hpp"

using nodecore::Json;

int main() {
    {
        // Keys come out lexicographic whatever order they went in, which is what
        // makes the Python adapter's sort_keys=True payload comparable.
        Json object = Json::object();
        object.set("zulu", Json::integer(1));
        object.set("alpha", Json::integer(2));
        object.set("mike", Json::integer(3));
        CHECK_EQ(object.dump_compact(), std::string(R"({"alpha":2,"mike":3,"zulu":1})"));
    }
    {
        Json object = Json::object();
        object.set("b", Json::array());
        object.set("a", Json::boolean(true));
        CHECK_EQ(object.dump(), std::string("{\n  \"a\": true,\n  \"b\": []\n}"));
    }
    {
        Json object = Json::object();
        Json values = Json::array();
        values.push(Json::integer(1)).push(Json::integer(2));
        object.set("xs", std::move(values));
        CHECK_EQ(object.dump_compact(), std::string(R"({"xs":[1,2]})"));
        CHECK_EQ(object.dump(), std::string("{\n  \"xs\": [\n    1,\n    2\n  ]\n}"));
    }
    {
        CHECK_EQ(Json::string("a\"b").dump_compact(), std::string("\"a\\\"b\""));
        CHECK_EQ(Json::string("a\\b").dump_compact(), std::string("\"a\\\\b\""));
        CHECK_EQ(Json::string("a\nb\tc").dump_compact(), std::string("\"a\\nb\\tc\""));
        CHECK_EQ(Json::string(std::string("x\x01y")).dump_compact(), std::string("\"x\\u0001y\""));
        // Bytes above ASCII pass through as UTF-8: a mount path is quoted
        // verbatim, never transliterated (ensure_ascii=False).
        CHECK_EQ(Json::string("/data/\xE7\x9B\xAE\xE5\xBD\x95").dump_compact(),
                 std::string("\"/data/\xE7\x9B\xAE\xE5\xBD\x95\""));
    }
    {
        // Fixed precision with a point separator, whatever the host's locale is.
        CHECK_EQ(Json::number(1.5, 2).dump_compact(), std::string("1.50"));
        CHECK_EQ(Json::number(12.3459, 1).dump_compact(), std::string("12.3"));
        CHECK_EQ(Json::number(0.0, 6).dump_compact(), std::string("0.000000"));
    }
    {
        const std::string text = Json::unavailable("no denominator").dump_compact();
        CHECK_EQ(text, std::string(R"({"reason":"no denominator","state":"UNAVAILABLE"})"));
    }
    {
        Json object = Json::object();
        object.set("k", Json::integer(1));
        object.set("k", Json::integer(2));
        CHECK_EQ(object.dump_compact(), std::string(R"({"k":2})"));
        CHECK(object.has("k"));
        CHECK(!object.has("missing"));
    }
    {
        // A byte counter above 2^53 stays exact because it never becomes a double.
        CHECK_EQ(Json::uinteger(18544522978000ull).dump_compact(), std::string("18544522978000"));
        CHECK_EQ(Json::integer(-1).dump_compact(), std::string("-1"));
    }
    {
        Json object = Json::object();
        object.set("a", Json::string("x"));
        Json inner = Json::object();
        inner.set("y", Json::number(1.0, 3));
        object.set("b", std::move(inner));
        CHECK_EQ(object.dump(), object.dump());
        CHECK_EQ(object.dump_compact(), std::string(R"({"a":"x","b":{"y":1.000}})"));
    }
    return check::report("test_json");
}

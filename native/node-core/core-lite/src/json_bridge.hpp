// The bridge between the two existing JSON types in this repository.
//
// WHY A BRIDGE AND NOT A THIRD TYPE
// ---------------------------------
// `nodecore::Json` WRITES and never reads — the collector never had to consume a
// document. `nodecore::supervisor::detail::Json` READS and never writes — it was
// written for the desired-state manifest and the recovery state. The two exist
// because a reader and a writer fail differently: a reader must refuse a
// duplicate key, a writer must round a float. Neither is going to replace the
// other, and adding a third representation here would give this component its
// own opinion about what a number is.
//
// So Core-Lite reads with the supervisor's reader and writes with the
// collector's writer, and this file is the four-line-per-kind adapter between
// them. It is used for exactly one thing: a value fragment that arrived as
// JSON text is parsed (so a fragment that is not JSON is refused instead of
// spliced in as text, where it would become a document nobody can read) and
// re-emitted through the one writer that already knows how to escape a string.
//
// It lives in `src/`, not `include/`: nothing outside this component gets to
// depend on it, so it can change without a contract review. That is the same
// reason the supervisor keeps its own `json_detail.hpp` here.
#pragma once

#include <string>

#include "nodecore/json.hpp"
#include "json_detail.hpp"

namespace nodecore::corelite::detail {

//: Convert a parsed fragment into the collector's writer type.
inline nodecore::Json to_nodecore(const nodecore::supervisor::detail::Json& node) {
    using Parsed = nodecore::supervisor::detail::Json;
    switch (node.kind) {
        case Parsed::Kind::Null:
            return nodecore::Json::null();
        case Parsed::Kind::Bool:
            return nodecore::Json::boolean(node.boolean);
        case Parsed::Kind::Int:
            return nodecore::Json::integer(node.integer);
        case Parsed::Kind::Real:
            // Precision 6 keeps a measured rate readable without pretending to
            // more digits than a counter delta actually has.
            return nodecore::Json::number(node.real, 6);
        case Parsed::Kind::String:
            return nodecore::Json::string(node.text);
        case Parsed::Kind::Array: {
            nodecore::Json array = nodecore::Json::array();
            for (const auto& item : node.items) {
                array.push(to_nodecore(item));
            }
            return array;
        }
        case Parsed::Kind::Object: {
            nodecore::Json object = nodecore::Json::object();
            for (const auto& member : node.members) {
                object.set(member.first, to_nodecore(member.second));
            }
            return object;
        }
    }
    return nodecore::Json::null();
}

//: Parse a fragment and re-emit it through the collector's writer. Returns
//: false with a reason when the text is not one complete JSON value — the caller
//: turns that into an UNAVAILABLE reading rather than emitting the raw text.
inline bool canonicalise(const std::string& fragment, std::string& out, std::string& error) {
    nodecore::supervisor::detail::Json parsed;
    nodecore::supervisor::detail::Reader reader(fragment);
    if (!reader.parse(parsed, error)) {
        return false;
    }
    out = to_nodecore(parsed).dump_compact();
    return true;
}

}  // namespace nodecore::corelite::detail

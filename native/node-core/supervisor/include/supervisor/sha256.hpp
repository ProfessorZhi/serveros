// SHA-256 (FIPS 180-4), in-tree, for one job: the integrity record a receipt
// carries for the manifest it acted on.
//
// WHY IT IS HERE RATHER THAN A DEPENDENCY
// ---------------------------------------
// This tree builds on a host with a C++20 compiler and nothing else, and the
// JSON writer was kept in-tree for the same reason. SHA-256 is a fixed,
// published algorithm with official test vectors, so "we wrote it" costs a
// reader nothing to check.
//
// WHAT IT IS NOT
// --------------
// It is NOT a signature and the Core holds NO key. A digest proves the bytes
// a receipt names are the bytes that were read; it does not prove who wrote
// them, and a reader who upgrades this into an authorisation has invented a
// security property out of a checksum. Signature verification needs a key
// path that this round does not have, and `README.md` says so where a reader
// will find it.
#pragma once

#include <string>

namespace nodecore::supervisor {

// Lowercase hex, 64 characters. `sha256_hex("")` is
// "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855".
std::string sha256_hex(const std::string& data);

}  // namespace nodecore::supervisor

// SHA-256 against the published vectors, plus the lengths where padding bugs
// live.
//
// A hand-written hash is exactly the kind of code that "looks right" and is
// wrong for one input length, so this file leans on the FIPS/RFC test vectors
// rather than on a round-trip against itself. The one-million-'a' case is RFC
// 6234's long-message vector and is the only slow one here (~1 MB hashed).
#include <cctype>
#include <string>

#include "check.hpp"
#include "supervisor/sha256.hpp"

using nodecore::supervisor::sha256_hex;

int main() {
    // FIPS 180-4 / RFC 6234 §8.5 and the appendix cases.
    CHECK_EQ(sha256_hex(""),
             std::string("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"));
    CHECK_EQ(sha256_hex("a"),
             std::string("ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"));
    CHECK_EQ(sha256_hex("abc"),
             std::string("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"));

    // 56 bytes — one byte short of the length field's first padded block.
    CHECK_EQ(sha256_hex("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
             std::string("248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"));

    // 112 bytes — two full blocks, so the length lands in the third.
    CHECK_EQ(sha256_hex("abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmno"
                        "ijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu"),
             std::string("cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1"));

    // The boundaries a padding bug hides in. A message of 55 bytes still fits
    // its length field in one block; 56 and 63 force a second block; 64 is
    // the first exact multiple and must not grow a third.
    //
    // These four digests are NOT this implementation's output. They were
    // computed by an independent implementation —
    //   python3 -c "import hashlib;print(hashlib.sha256(b'a'*N).hexdigest())"
    // — because a table copied from the code under test agrees with that code
    // by construction and would prove nothing.
    CHECK_EQ(sha256_hex(std::string(55, 'a')),
             std::string("9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318"));
    CHECK_EQ(sha256_hex(std::string(56, 'a')),
             std::string("b35439a4ac6f0948b6d6f9e3c6af0f5f590ce20f1bde7090ef7970686ec6738a"));
    CHECK_EQ(sha256_hex(std::string(63, 'a')),
             std::string("7d3e74a05d7db15bce4ad9ec0658ea98e3f06eeecf16b4c6fff2da457ddc2f34"));
    CHECK_EQ(sha256_hex(std::string(64, 'a')),
             std::string("ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb"));

    std::string million(1000000, 'a');
    CHECK_EQ(sha256_hex(million),
             std::string("cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"));

    // Shape: lowercase hex, always 64 characters. A digest that is sometimes
    // 63 or has an upper-case nibble would compare unequal for a reason that
    // has nothing to do with integrity.
    const std::string digest = sha256_hex("serveros");
    CHECK_EQ(digest.size(), std::size_t{64});
    for (char c : digest) {
        CHECK((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));
    }

    return check::report("test_sup_sha256");
}

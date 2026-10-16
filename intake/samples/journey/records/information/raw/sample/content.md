# 合成飞书文档（fixture）

这个文件不是任何真实的飞书文档。它是给 B1 读取者核对**字节**用的：
`intake/source_reading.py#read_packet` 会对它算 sha256，并与同目录 `manifest.json`
里的 `content_sha256` 比对；不一致就拒绝。

所以「来源修订」在这条链上不是一个说法，而是一次可以被复核的读数。

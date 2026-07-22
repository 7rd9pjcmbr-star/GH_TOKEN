# Atlas mật mã học — tài liệu tham chiếu

Nguồn dữ liệu tương tác: [`/atlas/`](../atlas/index.html) · JSON/JS: [`/data/crypto-atlas.js`](../data/crypto-atlas.js)

## Phân loại kiến thức

| Nhóm | Nội dung |
|------|----------|
| Nền tảng | Encryption ≠ encoding, CIA, AEAD |
| Cổ điển | Caesar, Vigenère, Playfair (học thuật) |
| Đối xứng | AES-GCM, ChaCha20-Poly1305 |
| Bất đối xứng | RSA, ECC, chữ ký, hybrid / HPKE |
| Hash · MAC · KDF | SHA-2/3, HMAC, Argon2, HKDF |
| Giao thức | TLS 1.3, Signal, OpenPGP, JOSE |
| Hậu lượng tử | ML-KEM, ML-DSA, liboqs |
| Biểu diễn | Base64, Hex, Morse, Braille |

## Bản đồ thư viện (tóm tắt)

### Engine / nền tảng
OpenSSL · BoringSSL · AWS-LC · Botan · Crypto++

### API hiện đại (giảm misuse)
libsodium / NaCl · Google Tink · TweetNaCl

### Theo ngôn ngữ / nền
| Hệ | Thư viện chính |
|----|----------------|
| Python | pyca/cryptography, PyNaCl, PyCryptodome |
| Go | crypto/*, x/crypto |
| Rust | RustCrypto, ring, rustls, Sequoia |
| JS / Browser | Web Crypto, Node crypto, libsodium.js, OpenPGP.js |
| Java / Kotlin | JCA/JCE, Bouncy Castle |
| .NET | System.Security.Cryptography |
| Apple | CryptoKit, Security.framework |
| Embedded | Mbed TLS, wolfSSL |

### PQC & công cụ
liboqs · age/rage · GnuPG / Sequoia · JOSE stacks

## Quy tắc chọn nhanh

1. Dữ liệu ứng dụng → libsodium / Tink / AES-GCM  
2. HTTPS → TLS 1.3 (stdlib / rustls / BoringSSL)  
3. Browser → Web Crypto (+ libsodium.js nếu thiếu)  
4. Mật khẩu → Argon2id  
5. PQC → hybrid ML-KEM + X25519 qua liboqs  

## Cảnh báo

- Không nhầm **encoding** (Base64, Morse) với **encryption**.
- Không tự viết cipher cho hệ thống thật.
- Không reuse nonce với AES-GCM / ChaCha20.

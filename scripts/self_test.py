"""Small deterministic check for the bundled ISAAC64 implementation."""

from isaac64 import decrypt_prefix


def main() -> None:
    clear = b"\x00\x00\x00\x18ftypisom" + b"test-data" * 64
    key = 123456789
    encrypted = decrypt_prefix(clear, key)
    assert encrypted != clear
    assert decrypt_prefix(encrypted, key) == clear
    print("self-test passed")


if __name__ == "__main__":
    main()

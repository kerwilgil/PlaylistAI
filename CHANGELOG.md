# Changelog

## 1.2.0

- OpenAI and NVIDIA NIM models load from the configured provider's catalog. Model settings support manual refresh, caching, and saved selections. Inclusion in the catalog does not guarantee text generation support.
- The model selector has rounded edges, keyboard navigation, and a responsive layout. Spanish catalog messages use UTF-8.
- Spotify access and refresh tokens stay in process memory. The browser holds a session identifier, and restarting the app requires reconnecting Spotify.
- Windows and macOS build scripts use isolated environments and a dependency lock with hashes.
- Tests cover catalog failures and refresh, OAuth state, session expiry, logout, replay, and concurrent token refresh.

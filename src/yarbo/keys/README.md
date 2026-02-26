# RSA Key — PLACEHOLDER

> ⚠️ **This is a placeholder key only — cloud auth will fail until you replace it.**

The file `rsa_public_key.pem` in this directory is a **placeholder RSA 2048-bit
public key**. It is structurally valid but is **NOT the Yarbo app's real key**.
Cloud login (`YarboCloudClient`) requires the real key to encrypt the password.

## How to get the real key

The actual public key is bundled inside the Yarbo Android APK at:

```
assets/rsa_key/rsa_public_key.pem
```

### Extract from APK

```bash
# 1. Download the Yarbo APK (from Google Play via apkpure or your device)
# 2. Unzip it (APKs are ZIP archives)
unzip yarbo.apk -d yarbo_unpacked

# 3. Copy the key
cp yarbo_unpacked/assets/rsa_key/rsa_public_key.pem /path/to/python-yarbo/src/yarbo/keys/
```

### Supply at runtime (alternative)

If you prefer not to vendor the key, pass its path when constructing the client:

```python
from yarbo import YarboCloudClient

async with YarboCloudClient(
    username="user@example.com",
    password="your_password",
    rsa_key_path="/path/to/rsa_public_key.pem",
) as client:
    robots = await client.list_robots()
```

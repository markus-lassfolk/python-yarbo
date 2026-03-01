# RSA Key — PLACEHOLDER

> ⚠️ **This is a placeholder key only — cloud auth will fail until you replace it.**

The file `rsa_public_key.pem` in this directory is a **placeholder RSA 2048-bit
public key**. It is structurally valid but is **NOT the Yarbo app's real key**.
Cloud login (`YarboCloudClient`) requires the real key to encrypt the password.

## How to get the real key

The actual public key can be obtained from the Yarbo app package and placed at:

```
src/yarbo/keys/rsa_public_key.pem
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

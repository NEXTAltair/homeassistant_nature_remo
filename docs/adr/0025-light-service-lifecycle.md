# Light service lifecycle (#25)

Registering the service during each config entry setup captured only the latest entry and kept stale light references after unload.

Register the integration service once in `async_setup`. Resolve the requested entity through the entity registry on every call and validate its owning config entry state. Each entry keeps only its active light instances, keyed by stable registry ID and removed with the entity lifecycle. Sending and post-success state publication belong to the light instance.

This preserves the service name, input fields and flat response. An entity service was not selected because its response is grouped by entity ID. Translated validation errors now reject missing, disabled, unloaded and unsupported targets before a request.

No extra polling or entity ID changes are introduced. Tests cover both multi-entry setup orders, unload/reload, disable, rename, setup failure, invalid modes and API failures. AGENTS.md and both exception translations describe the lifecycle.

# Nature account identity (#24)

The config flow previously stored unchecked tokens and permitted repeated registrations. Duplicate entries poll the same account and compete for entity unique IDs.

Use the stable ID from `/users/me` as the config entry unique ID. Reject identical tokens before requesting account metadata and reject already configured or in-progress account IDs. Reauthentication checks account identity before saving credentials.

Existing entries are identified after the normal first refresh. This optional migration cannot block setup or start reauthentication. An entry keeps its missing unique ID if another entry owns that account; a dismissible repair lists the duplicate entry titles. Verified legacy identities stay in memory across unloads so deleting either entry can resolve the repair, and setup rebuilds repairs after restart.

This adds at most one metadata request per setup for a legacy entry and none to periodic polling. Entity IDs, token storage and the existing single-reload behavior remain stable. Tests cover validation, concurrent flows, migration failures and duplicate removal in either order; English and Japanese documentation and translations describe the behavior.

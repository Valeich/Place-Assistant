# Google Business Profile API — Findings

**Question evaluated:** Can the Google Business Profile API (formerly Google My Business API) be used to find/collect data about *other* businesses (e.g. cafes and bars that serve alcohol in an area)?

**Answer: No.** This is confirmed directly from Google's own developer documentation, not assumption.

## What the API actually is

Business Profile API is an **owner-management tool**, not a discovery/search API. It lets a business owner or manager read and update *their own* listing — hours, photos, responding to reviews, etc. It has no endpoint for searching or browsing businesses in general.

## Why it can't be used for discovery

1. **Auth model:** every request requires OAuth 2.0 user consent (scope `business.manage`) — there is no API-key path. The resulting access token is scoped only to Business Profile locations that specific Google account is a verified owner/manager of.
   Source: [developers.google.com/my-business/content/implement-oauth](https://developers.google.com/my-business/content/implement-oauth)

2. **Even a successful call only returns what you manage.** The core listing endpoint (`GET https://mybusinessaccountmanagement.googleapis.com/v1/accounts`) returns `{"accounts": []}` for an authenticated user managing zero verified listings — not an error, just an empty result. There is no parameter to query other businesses.
   Source: [developers.google.com/my-business/reference/accountmanagement/rest/v1/accounts/list](https://developers.google.com/my-business/reference/accountmanagement/rest/v1/accounts/list)

3. **Access itself is gated behind an approval application** — this isn't a simple "enable in Cloud Console" API. To even qualify to apply, an applicant needs a Business Profile that has been **verified and active for 60+ days, with a business website listed on it**. Approval is not instant.
   Source: [developers.google.com/my-business/content/prereqs](https://developers.google.com/my-business/content/prereqs), [developers.google.com/my-business/content/basic-setup](https://developers.google.com/my-business/content/basic-setup)

So even in the best case — approved access, valid OAuth login — the API structurally cannot answer "which cafes in Jakarta Pusat serve alcohol." It can only ever answer "what does my own business's listing say."

## What actually does this

**Places API (New)** is the correct tool for querying business/place data Google surfaces on Maps (name, address, rating, reviews, hours, alcohol service via `servesBeer`/`servesWine`/`servesCocktails`, etc.) for businesses you don't own. This is what this project's `area_collector` agent (`place_assistant/pipeline/`) already uses — fully built, including an alcohol-service filter, currently blocked only on enabling billing on the GCP project holding the Maps API key (a Google Cloud Console step, unrelated to Business Profile API).

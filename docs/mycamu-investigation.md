# UWA India MyCamu investigation

## Portal

- Target: [UWA India MyCamu](https://student.india.uwa.edu.au/v2/), for the Mumbai/Chennai project specified by the user.
- Investigated on 9 October 2026. Public research and an unauthenticated HTML GET only; no browser automation, login, session reuse, or academic API requests.
- Labels below distinguish **confirmed** source statements/direct observations, **observations** that require validation, and **unknowns**. Vendor-wide capabilities do not establish this tenant's configuration or permission to use them.

## Public findings

- **Confirmed — vendor claim:** Camu describes secured, documented REST APIs for interoperability. The reviewed page does not provide an endpoint reference or third-party registration procedure. [Camu product technology](https://camudigitalcampus.com/product-technology/)
- **Confirmed — vendor claim:** Camu lists OAuth 2.0, Microsoft AD, Google Authentication and Keycloak, plus webhooks for student/enrolment/payment datasets and LTI/SCORM capabilities. These are product capabilities, not evidence of UWA India enabling academic API access. [Camu interfacing capabilities](https://camudigitalcampus.com/camu-interfacing-capabilities/)
- **Confirmed — UWA India guidance:** MyCamu supports enrolment, class timetables, academic records and university announcements; the LMS provides assessments, learning materials and unit information. [UWA India student glossary](https://india.uwa.edu.au/current-students/student-glossary)
- **Confirmed — vendor feature claim:** MyCamu can present assignments, timetables and exam schedules. Availability of these features in UWA India's instance remains unverified. [Camu mobile solutions](https://camudigitalcampus.com/camu-mobile-solutions/)
- **Unknown:** Searches of public Camu/MyCamu information did not locate a usable academic developer specification, documented GraphQL API, or supported ICS/iCalendar export. This is not proof that these do not exist. Unrelated campusM, Camunda and other campus software documentation was excluded.

## Likely architecture

**Confirmed public observation:** A direct GET to the target returned HTTP 200, `text/html; charset=UTF-8`, and a small HTML shell (1,035 decoded characters). It contained a `div` with `id="root"` and script references:

```text
/v2/static/js/main.68b861de44.js
/v2/assets/myCamu-ChatBot-widget/index.umd.js
```

**Reasonable observation:** The root mount and bundled script are consistent with a JavaScript SPA. No JavaScript was executed; client routing/framework and runtime requests were not verified. The bundle filename is deployment-specific, not an API endpoint.

**Unknown:** REST vs GraphQL vs RPC/another mechanism for this frontend. Camu's general REST claim supports investigating REST, but does not establish how this tenant obtains any particular dataset. No academic endpoint has been identified or guessed.

## Available integration methods

| Candidate | Evidence and suitability |
| --- | --- |
| Official REST API | Product-level support confirmed; best candidate if UWA/Camu supplies documentation, approved read access and a supported authentication flow. Tenant availability unknown. |
| ICS/calendar export | No supported feed confirmed. If available, potentially useful for timetable/calendar only; verify assignment coverage, stable event IDs, timezone, updates and cancellations. |
| Approved export/report | Ask whether a supported student-specific JSON/CSV export exists; availability and refresh mechanism unknown. |
| Webhooks/LTI/SCORM | Advertised by Camu, but not demonstrated as read APIs for this bot's timetable/assignment needs. |
| Frontend internal requests | Diagnostic evidence only. Discovering a request does not make it a supported third-party integration. |

## Authentication

**Confirmed:** Vendor-wide OAuth 2.0 support is advertised in the interfacing source above.

**Unknown:** UWA India's login mechanism, OAuth grants, consent, scopes, client registration, refresh/revocation rules, or whether delegated student read access is offered. SSO support is not proof that browser authentication can be reused by a bot. No login or token endpoint was tested.

**Approval assessment:** Exact UWA/Camu approval requirements are not publicly established. Request confirmation from UWA India IT/Camu administration before implementing third-party access. Ask for approved documentation and registration instructions, not credentials in chat.

## Mumbai/Chennai handling

**Unknown:** Whether responses explicitly contain campus names/IDs or instead encode them through institution, programme, section or enrolment relationships. Public HTML provided no academic response schema. Do not infer campus from hostname, course code, room name or another institution's examples.

Ask for sanitized field names and a confirmed mapping of campus identifiers to Mumbai/Chennai. Inspection of one student's own account cannot establish another campus's data shape. Until verified, preserve campus as unknown; the current database's null value means **both**, so it must not be used for missing campus information during a future sync.

## Data we can likely access

This describes likely portal-visible information, **not confirmed API access**:

| Data | Evidence / remaining question |
| --- | --- |
| Courses/enrolments | UWA confirms enrolment management; unit lists, stable identifiers and exact response fields need inspection. |
| Timetable | UWA explicitly confirms MyCamu class times; start/end timestamps, locations, recurrence and cancellations need inspection. |
| Assignments/due dates | Camu supports assignments generally; UWA directs assessment access to its LMS. MyCamu assignment coverage is unknown. |
| Calendar events | Timetable functionality is supported by UWA guidance; a separate calendar feed or endpoint is unconfirmed. |
| Announcements | UWA confirms notices in MyCamu; API availability and permissions are unknown. |

## Data still unknown

Official API base/version and schemas; REST/GraphQL/RPC choice; student scoping; pagination; rate limits; timestamp timezone; stable source IDs; deleted/changed records; campus mapping; assignment source of truth; export/feed availability; and permitted Discord storage/display.

## Security/authorization constraints

- Read-only, authorized access to the student's own records only. No endpoint guessing, request replay, identifier swapping, GraphQL introspection probing, or other-student inspection.
- Do not automate login/SSO/MFA, copy browser credentials into the bot, scrape authenticated UI, or store university passwords.
- Do not share Authorization/private auth headers, cookies, bearer/refresh/CSRF tokens, SAML payloads, session IDs, passwords, MFA values, signed URLs or private calendar subscription URLs. Do not export HAR or copy requests as cURL/fetch.
- A discovered internal API or calendar link is not authorization to use it. Any future approved feed secret must be handled as a credential, never pasted into this report or committed.

## Manual DevTools inspection needed

1. Sign in manually to **your own** account. Open Chrome DevTools → Network → Fetch/XHR **after login**. Leave Preserve log off and clear the list.
2. Navigate normally, one screen at a time: timetable, calendar if present, enrolled courses/units, assignments, and optionally attendance. Record which screen causes which request. Do not submit assignments or change enrolment/attendance.
3. For relevant requests, transcribe only: screen name; sanitized origin/path; HTTP method; status; response content type; query/body **key names only**; response field names/types/nesting; whether pagination exists. Remove query values, fragments, personal IDs and secret-bearing path segments. Use consistent placeholders for IDs, including pagination cursors.
4. Describe fields for course code/title, assignment deadline, event start/end/location, source IDs, campus/section/institution, and timezone/offset. Report whether Mumbai/Chennai appears explicitly; sanitize identifiers and remove names, emails, student numbers, grades, attendance values and unrelated records. A tiny invented-value JSON schema is preferable to a raw response.
5. If a request appears GraphQL-like, report only whether `operationName`, `query` and `variables` keys occur and whether the response uses `data`/`errors`. Do not send the full request body. A path containing “graphql” alone is insufficient evidence. Likewise, JSON over HTTP alone does not establish REST.
6. Look for an official Export / Subscribe / Add to calendar control. Report its label, whether it offers a file or subscription, and whether a download has `.ics`/`text/calendar`. Do not share subscription URLs or file contents containing personal information. Such downloads may appear under Network → All rather than Fetch/XHR.
7. If no useful request appears, report that rather than trying alternative paths or replaying requests. Do not share screenshots containing headers or full network dumps.

Safe return format: `screen | sanitized origin/path | method | status | content type | field names/types | pagination? | campus evidence?`. This is observational evidence only, not permission to call the endpoint independently.

## Stage 7 recommendation

**Not currently ready for implementation: no safe, tenant-specific integration method is confirmed.** Keep `MyCamuIntegration` stubbed.

Next evidence needed:

1. The sanitized manual observations above, especially timetable/course schema and whether assignments/calendar exports actually exist.
2. From UWA India IT/Camu: the official API or feed documentation for this tenant; permission for this read-only Discord prototype; supported per-student authentication/registration and read scopes; required approvals; campus mapping; rate limits; and permitted storage/display. Ask whether assignment deadlines should instead come from the LMS.
3. If only an internal browser-session API exists, do not implement session replay. Request a supported integration or export. Sanitized network evidence alone does not satisfy the authorization/authentication requirement.

Prefer an approved official API; use an institution-supported ICS feed only for the data it demonstrably covers. Proceed to Stage 7 only after the method and authorization are confirmed and the user explicitly requests that stage.

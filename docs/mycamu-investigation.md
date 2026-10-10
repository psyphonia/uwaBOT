# UWA India MyCamu investigation

## Portal

- Target: [UWA India MyCamu](https://student.india.uwa.edu.au/v2/), for the Mumbai/Chennai project specified by the user.
- Initially investigated on 9 October 2026; extended on 10 October 2026. Public research and unauthenticated HTML/static JavaScript GETs only; no browser automation, login, session reuse, or academic API requests.
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

**Confirmed static-code observations (10 October):** The main script is a Webpack runtime with React, Axios and Microsoft MSAL dependency registrations. Its initial chunk list and hash map explicitly reference [application chunk 4627](https://student.india.uwa.edu.au/v2/static/js/4627.2565661654.js), which was publicly readable. No JavaScript was executed. Another explicitly referenced startup chunk returned HTTP 403; inspection of that asset stopped.

The application chunk contains an Axios client created with `baseURL: "/"` and `withCredentials: true`. Its URL resolver (Webpack module 98638, export `G`) prepends `/api` to most named routes, with explicit exceptions such as login. HTTP wrapper module 2377 forwards `get`/`post` calls to Axios. Together these provide evidence beyond filenames or isolated strings.

**Observed frontend API root:** `https://student.india.uwa.edu.au/api` for resolver-generated academic paths. This is an internal frontend request prefix, **not a verified official third-party API base/version**. Runtime responses and server routing remain untested.

### Academic request inventory — static verification

**Confidence:** High = route resolver, HTTP call and caller payload/consumer traced; Medium = HTTP call/path traced but payload or consumer incomplete; Low = route constant only. These levels describe frontend evidence, **not successful server responses or approval**. No listed backend path was called.

| Dataset / path | Method | Observed request body / parameters | Expected response after frontend adapter | Confidence |
| --- | --- | --- | --- | --- |
| Timetable: `/api/Timetable/get` | POST | `PrID`, `CrID`, `AcYr`, `DeptID`, `SemID`, `SecID`, `start`, `end`, `usrTime`, `schdlTyp`, `isShowCancelledPeriod`, `isFromTt`; optional `aEnrolProgs` and `oCurrPrg`. Screen uses the selected day for start/end, `schdlTyp="slctdSchdl"`, and both flags true. | Array; first element consumes `config`, `Periods[]`, `Holiday[]`, optional `vcDtls`. Periods use `start`, `end`, `_id`, `crsTyp`; holidays consume `title`, `eTy`, `WrkngTyp`. Timestamp offset/recurrence/cancellation contract unverified. | High |
| Enrolled/registration subjects: `/api/fetchPastEnrolSubjs` | POST | `AcYr`, `InId`, `CrID`, `PrID`, `DeptID`, `SemID`, `StuID`, `CrsPlan`, `type`; screen initially uses `type="UPCOMING"`. This concerns registration subjects, not necessarily all active teaching units. | Array, with registration timing/status fields consumed from the first item (`EnrlSrtTimer`, `EnrlLTimer`, `ackDate`); subject detail view uses `SubCd`, `SubjNm`. Exact nesting unverified. | High |
| Student subject lookup: `/api/subject/get-student-subject` | POST | Assignment screen forwards its current filter/progression object when `StuID` exists: same academic/student fields as the assignment request below; exact required subset unknown. | Array; subject selector consumes `SubId` and `SubNa`. Meaning of ID vs human course code must be validated. | High |
| Assignments: `/api/Assignment/getAssignment` | POST | `InId`, `PrID`, `CrID`, `DeptID`, `SemID`, `SecID`, `AcYr`, `AcyrFrDt`, `AcyrToDt`, `StuID`, `LginId`, `isFE`, `getVerified`, `getRange`, `docsPerPage`, `pageNo`; optional `filterStDate`, `filterEndDate`, `filterSubj`. Initial screen values include `getVerified="yes"`, `getRange="upcoming"`, `docsPerPage=20`, `pageNo=1`. | Object with `data[]` and `pageData.itemCount`. Items consume `CmAssID`, `Title`, `SubId`, `SubNa`, `assgndDt`, `assgnDueDt`, `endTme`, `submitted`, `verified`, `Attachments`. Code combines due date with `endTme` when present; do not assume midnight or UTC. | High |
| Holidays/calendar events: `/api/HolidayDefinition/getUpcomingEvents` | POST | Selected progression object spread into body, plus `getRange` (screen uses `upcoming`/`all`) and `isForMobile=true`. Required progression fields not specified by this caller. | Array; calendar transforms `_id`, `Name`, `HlTyp`, `HldDtFrom`, `HldDtTo` to events. This is the Holidays screen, not proof of a complete student calendar feed. | High |
| Attendance summary: `/api/Attendance/getDtaForStupage` | POST | `InId`, `PrID`, `CrID`, `DeptID`, `SemID`, `AcYr`, `SecID`, `CmProgID`, `StuID`; added `isFE`, `isForWeb=true`, `isFrAbLg=false`, `isMinAttPer=true`; optional `oEnrlPrg` with `CrsPlan`/`CrID`. | Object; screen consumes `subjectList`, `OvrAllCnt`, `OvrAllPCnt`, `OvrAllPrcntg`, `CurMCnt`, `CurMPCnt`, `CurMnthPrcntg`, `minAttPer`, `isPerAttPrst`. Detail item schema unverified. | High |
| Attendance by type/subject: `/api/getAttendanceByAttTypAndSubj` | POST | Service forwards a caller-supplied object. Exact field names/required parameters not established; do not invent them from the endpoint name. | Service extracts `data`/`errors` and returns `data`; nested shape not established. | Medium |

The shared Axios response interceptor returns `response.data.output` when present, otherwise `response.data`. Academic service functions then destructure `data` and `errors`. A **code-inferred**, not captured, wire envelope is therefore `{output: {data: <payload>, errors: ...}}`; the adapter also permits an unwrapped alternative. The table describes the inner payload expected by consumers, not a verified server schema.

Additional **Low-confidence constants**, with no verified method/payload/response: `/api/getStudentTimetable`, `/api/course/getcourse`, `/api/course/get-course-by-inid-prid`, `/api/getEnrolledSectionByStudID/`. Keep these as unverified alternatives, not callable recommendations. In particular, a Camu “course” may mean programme; student subjects above are better evidenced for teaching units.

### Evidence locations

Public source URLs are deployment-specific; module IDs and hashes can change:

- [Main runtime](https://student.india.uwa.edu.au/v2/static/js/main.68b861de44.js): chunk hash map and explicitly referenced initial assets.
- [Application chunk 4627](https://student.india.uwa.edu.au/v2/static/js/4627.2565661654.js): resolver module `98638` (`G`), HTTP wrapper `2377`, attendance service `75415` (`i` summary / `g` detail), and lazy screen references.
- [Timetable chunk 6328](https://student.india.uwa.edu.au/v2/static/js/6328.9b2af6d844.js): screen request builder `ye`, export `t3` mapping to the `TIMETABLE_GET` POST service, and period/holiday consumers.
- [Assignments chunk 7970](https://student.india.uwa.edu.au/v2/static/js/7970.47878d8398.js): `oe` request/consumer, initial payload effect, student subject lookup and deadline handling.
- [Course registration chunk 7243](https://student.india.uwa.edu.au/v2/static/js/7243.7876aa2557.js): `FETCHPASTENROLSUBJS` POST and its registration/subject consumers. Write-side registration confirmation code is excluded.
- [Attendance chunk 6437](https://student.india.uwa.edu.au/v2/static/js/6437.e7404220ae.js): imports service `75415`, constructs summary payload and reads summary fields.
- [Holidays chunk 8249](https://student.india.uwa.edu.au/v2/static/js/8249.d667ad7087.js): service module `97469`, `HOLIDAYDEFINITION_GETUPCOMINGEVENTS` POST, progression payload and holiday-to-calendar mapping.

Only assets referenced by the public runtime/screen code were fetched. A previously denied startup asset was not retried or accessed through an alternative. No DevTools-detection changes, execution, login, cookie jar, bearer token or authenticated request was used. Payload names above come from source code; no real identifiers were collected. Requiredness, validation, tenant enablement and pagination guarantees remain unknown.

**Architecture assessment:** React SPA with Axios HTTP requests, including POST-based named academic operations. This resembles an HTTP/JSON RPC-style backend; it is insufficient to claim RESTful semantics for every route. No GraphQL request construction was established in the inspected assets; that does not rule out GraphQL elsewhere.

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

**Confirmed static-code observations:** The inspected frontend uses credentialed Axios requests and constructs an `Authorization` header with a Bearer scheme. Its request interceptor reads locally stored login/application state and adds application-version, timezone-offset and selected-course metadata. These are code observations, not collected credentials or a statement that cookies are required by every endpoint.

The route resolver maps `LOGIN` to `/login/validate` without `/api`; a login service constructs a POST to it. This identifies the frontend's login call but is **not** a supported bot authentication mechanism and was not invoked. The bundle also registers Microsoft MSAL dependencies and has external-auth configuration logic; their presence does not prove which identity provider or SSO flow is enabled for UWA India.

**Unknown:** Bearer token issuance/lifetime/refresh, whether cookie state is also required, tenant-enabled identity provider, OAuth grants, consent, read scopes, client registration/revocation, or delegated student access. No login, auth configuration, token or academic endpoint was tested. Do not recreate the frontend password submission or reuse browser sessions.

### Supported third-party OAuth / client credentials

**Finding: not confirmed.** Camu's [interfacing page](https://camudigitalcampus.com/camu-interfacing-capabilities/) advertises OAuth 2.0; its [technology page](https://camudigitalcampus.com/product-technology/) advertises documented REST APIs. Neither provides a token endpoint, supported grants, API audience/read scopes, third-party client registration, consent procedure or a `client_credentials` contract. Targeted public searches did not find such a Camu contract; unrelated Canvas/Camunda documentation was excluded.

MSAL/browser sign-in support in frontend dependencies is not proof of an approved app-to-app grant for Camu academic APIs. No supported authorization-code/PKCE or client-credential flow for this bot can be specified yet. Do not derive one from login code or construct guessed OAuth endpoints.

Ask UWA India IT/Camu whether a registered read-only app may use delegated student authorization or a restricted institution-managed service identity; request the official grant/issuer/audience/scope documentation and explicit Mumbai/Chennai/student scope. If client credentials are offered, confirm which student records the app may read: app authentication alone does not authorize access to every student. Exact approval requirements remain unconfirmed.

**Approval assessment:** Exact UWA/Camu approval requirements are not publicly established; no public self-service application registration or read-scope contract was found. Request confirmation from UWA India IT/Camu administration before implementing third-party access. Ask for approved documentation and registration instructions, not credentials in chat.

### Live authentication review — 10 October 2026

**Decision: no supported live authentication path can currently be confirmed.** No authentication implementation was added. The adapter remains offline with injected authentication/transport; its missing-provider failure remains in place. No login, external-auth configuration, token or academic endpoint was called, and no browser state was read.

**Confirmed public static observations:** Reinspection of application chunk `4627` shows:

- `EXTERNAL_AUTH_CONFIG` resolves to `/external/auth/get_external_auth_config`; the service uses POST and a sign-in component supplies `{authSrc: ...}`. It consumes `config.google.id` or `config.msAuth.id`. This is a browser configuration lookup, not a confirmed registration/token endpoint for third-party apps.
- A Microsoft browser sign-in branch constructs MSAL configuration using a public application ID, authority `https://login.microsoftonline.com/common`, and a redirect URI derived from the portal origin plus `/v2`. Another branch uses an ID supplied by external-auth configuration. Public client IDs were not copied into the bot.
- Sign-in code invokes `loginPopup`, `acquireTokenSilent` and an interactive fallback `acquireTokenPopup`; one branch requests `user.read`. It passes the result to the portal's login logic with `authSrc="ms"`. A Google branch passes its sign-in credential with `authSrc="gAuth"`. These are source observations, not confirmation of enabled UWA India providers or API token audiences.
- The academic request interceptor may set an `api-key` header from stored login state and an `Authorization: Bearer ...` header from stored JWT state. An `api-key` header name here does **not** establish a separately provisioned developer API key. Required combinations, issuance, expiry and refresh remain unknown.

Source: [public application chunk](https://student.india.uwa.edu.au/v2/static/js/4627.2565661654.js), service module `95762`, HTTP module `2377`, sign-in components and root MSAL configuration. Only static text was inspected; code was not executed. Previously denied assets were not revisited. No DevTools detection was bypassed.

**Reasonable observation:** The frontend supports identity-provider sign-in followed by portal login/session handling. Its browser flow cannot safely be treated as a delegated academic API contract, and its Microsoft authority/scope cannot be adopted as the bot's API authentication configuration.

**Official UWA guidance:** UWA India describes UWA ID and required MFA, and directs IT enquiries to its Service Desk. That guidance does not specify MyCamu API grants, scopes or app registration. [UWA India IT support](https://india.uwa.edu.au/current-students/it-support)

### Exact access requirements to request from UWA/Camu

Submit an enquiry through the [UWA India Service Desk](https://servicedesk.india.uwa.edu.au/) requesting referral to the MyCamu tenant administrator and Camu/Octoze integration support. Request the following **non-secret documentation and approval** for `uwa-india-discord-bot`, a read-only prototype accessing only the consenting student's own records:

1. **Tenant/API authorization:** Written confirmation that this UWA India tenant permits the bot to read courses/subjects, timetable, assignments/deadlines, holidays/calendar and attendance; which of the discovered frontend paths are supported for external clients, or their supported replacements; official base URL/version and API reference. Confirm Mumbai and Chennai coverage and any required UWA/Camu application approval, licensing or allowlisting.
2. **Supported authentication contract:** State explicitly whether delegated OAuth, an institution-managed client-credential identity, or a provisioned API key is supported for those academic endpoints. Supply the official issuer/tenant, authorization/token endpoints or published discovery URL, API audience/resource and exact read permissions/scopes. Explain whether portal cookies are required; a browser-session-only mechanism is unsuitable for this adapter.
3. **Registration for the chosen mechanism:** For delegated OAuth, provide a new approved client registration, allowed grant/PKCE requirements, permitted redirect URIs, consent procedure and MFA/conditional-access behavior. For client credentials, confirm the allowed grant and client authentication method, provision an approved identity, and document server-enforced restriction to the authorized student; unrestricted access to other students is unacceptable. For API keys, document institutional provisioning, read/student scope and approved header scheme. These are conditional requirements, not claims that any mechanism exists.
4. **Credential lifecycle:** Document token/key lifetime, supported renewal, refresh-token policy if applicable, revocation, rotation and required credential storage. Provision any secret/certificate/key through an approved secure channel into local deployment configuration; do not send secrets, passwords, tokens or certificates in chat or commit them. No university password may be needed by the bot.
5. **Student and campus binding:** Supply the supported way to discover the authenticated student's academic context and map campus identifiers to Mumbai/Chennai. Confirm that authorization is enforced server-side rather than by trusting submitted `StuID`/institution/programme identifiers. Provide sanitized response schemas, timestamp semantics and required request fields.
6. **Operational permission and validation:** Confirm rate limits, an approved test/sandbox account or consented test environment, and permitted retention/display in Discord, including who may see the test channel. Supply a documented read-only validation procedure before any live production sync.

**Evidence needed to unblock implementation:** An official tenant-specific authentication/API specification and confirmed app approval/registration covering the above scope. Sanitized DevTools observations can clarify schemas but cannot substitute for this approval or token-issuance contract. If no such access is offered, request an authorized export/feed instead and keep live API authentication disabled.

## Mumbai/Chennai handling

**Unknown:** Whether responses explicitly contain campus names/IDs or instead encode them through institution, programme, section or enrolment relationships. Public static consumers expose academic field names but no confirmed Mumbai/Chennai mapping. Fields such as `InId`, `PrID`, `CrID`, `DeptID`, `SecID` and `CmProgID` are not proven campus identifiers. Do not infer campus from hostname, course code, room name or another institution's examples.

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
| Attendance | Public code constructs the two POST calls listed above; summary fields are identified above; runtime student scoping and authorized third-party availability remain unknown. |

## Data still unknown

Official third-party API base/version and schemas; runtime confirmation of the static paths; student scoping; pagination; rate limits; timestamp timezone; stable source IDs; deleted/changed records; campus mapping; assignment source of truth; export/feed availability; and permitted Discord storage/display.

## Security/authorization constraints

- Read-only, authorized access to the student's own records only. No endpoint guessing, request replay, identifier swapping, GraphQL introspection probing, or other-student inspection.
- Do not automate login/SSO/MFA, copy browser credentials into the bot, scrape authenticated UI, or store university passwords.
- Do not share Authorization/private auth headers, cookies, bearer/refresh/CSRF tokens, SAML payloads, session IDs, passwords, MFA values, signed URLs or private calendar subscription URLs. Do not export HAR or copy requests as cURL/fetch.
- A discovered internal API or calendar link is not authorization to use it. Any future approved feed secret must be handled as a credential, never pasted into this report or committed.

## Manual DevTools inspection needed

1. Sign in manually to **your own** account. Open Chrome DevTools → Network → Fetch/XHR **after login**. Leave Preserve log off and clear the list.
2. Navigate normally, one screen at a time: timetable, calendar if present, enrolled courses/units, assignments, and attendance. Compare observed requests with the static candidates above; differences are useful evidence, not a reason to replay a request. Record which screen causes which request. Do not submit assignments or change enrolment/attendance.
3. For relevant requests, transcribe only: screen name; sanitized origin/path; HTTP method; status; response content type; query/body **key names only**; response field names/types/nesting; whether pagination exists. Remove query values, fragments, personal IDs and secret-bearing path segments. Use consistent placeholders for IDs, including pagination cursors.
4. Describe fields for course code/title, assignment deadline, event start/end/location, source IDs, campus/section/institution, and timezone/offset. Report whether Mumbai/Chennai appears explicitly; sanitize identifiers and remove names, emails, student numbers, grades, attendance values and unrelated records. A tiny invented-value JSON schema is preferable to a raw response.
5. If a request appears GraphQL-like, report only whether `operationName`, `query` and `variables` keys occur and whether the response uses `data`/`errors`. Do not send the full request body. A path containing “graphql” alone is insufficient evidence. Likewise, JSON over HTTP alone does not establish REST.
6. Look for an official Export / Subscribe / Add to calendar control. Report its label, whether it offers a file or subscription, and whether a download has `.ics`/`text/calendar`. Do not share subscription URLs or file contents containing personal information. Such downloads may appear under Network → All rather than Fetch/XHR.
7. If no useful request appears, report that rather than trying alternative paths or replaying requests. Do not share screenshots containing headers or full network dumps.

Safe return format: `screen | sanitized origin/path | method | status | content type | field names/types | pagination? | campus evidence?`. This is observational evidence only, not permission to call the endpoint independently.

## Stage 7 recommendation

**Live integration remains blocked:** internal frontend request construction has been identified, but no supported, authorized third-party authentication/integration method is confirmed. At the user's subsequent request, an offline adapter was implemented against synthetic mocked responses. It requires injected transport/authentication and explicit campus/field/timezone configuration; it is not connected to the live portal, bot startup or database sync. This does not establish live API compatibility or access permission.

Next evidence needed:

1. The sanitized manual observations above, especially timetable/course schema and whether assignments/calendar exports actually exist.
2. From UWA India IT/Camu: the official API or feed documentation for this tenant; permission for this read-only Discord prototype; supported per-student authentication/registration and read scopes; required approvals; campus mapping; rate limits; and permitted storage/display. Ask whether assignment deadlines should instead come from the LMS.
3. If only an internal browser-session API exists, do not implement session replay. Request a supported integration or export. Sanitized network evidence alone does not satisfy the authorization/authentication requirement.

Prefer an approved official API; use an institution-supported ICS feed only for the data it demonstrably covers. Proceed to Stage 7 only after the method and authorization are confirmed and the user explicitly requests that stage.

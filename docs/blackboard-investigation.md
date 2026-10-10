# UWA Blackboard investigation

## Target and evidence

Investigated 10 October 2026 for `https://lms.uwa.edu.au/ultra/institution-page`. This stage used public university guidance and official Anthology documentation/specifications only. No Blackboard UI scraping, tenant API calls, login, SSO/MFA automation, browser credential collection or integration implementation.

**Confirmed product capabilities** below describe official Learn REST APIs, not successful calls against UWA. **Tenant unknowns** require UWA administrator confirmation.

Sources:

- [Official API explorer](https://developer.anthology.com/portal/displayApi), whose public iframe links to the [Learn Swagger specification](https://devportal-docstore.s3.amazonaws.com/learn-swagger.json). Paths, fields and endpoint-specific permissions below were checked against that specification.
- [Official developer application registration](https://github.com/blackboard/anthologydevdocs/blob/main/docs/developer-portal/creating-rest-or-lti-application.md).
- [Official delegated OAuth documentation](https://github.com/blackboard/anthologydevdocs/blob/main/docs/blackboard/rest-apis/getting-started/3lo.md) and [client-credential authentication](https://github.com/blackboard/anthologydevdocs/blob/main/docs/blackboard/rest-apis/getting-started/basic-authentication.md).
- [Administrator registration and consent](https://help.anthology.com/blackboard/administrator/en/integrations/three-legged-oauth.html).
- [Official calendar guidance](https://github.com/blackboard/anthologydevdocs/blob/main/docs/blackboard/rest-apis/hands-on/calendar-api.md).

## Read APIs for the bot

All paths are relative to the approved Learn host (expected UWA origin: `https://lms.uwa.edu.au`). Use only GET academic operations. Different resources have different API versions; do not assume a single version prefix. List responses normally expose `results` and paging information; follow documented pagination with same-origin validation.

| Need | Official method/path | Useful data and limits |
| --- | --- | --- |
| Student's enrolled courses | GET `/learn/api/public/v1/users/{userId}/courses` | Membership course IDs and roles. Use the authorized student's identity, not a global course/roster search. Users can read their own memberships; distinguish organizations from teaching courses. |
| Course information | GET `/learn/api/public/v3/courses/{courseId}` | `id`, `courseId`, `name`, `description`, `termId`, availability and course-view metadata. Student-visible fields are restricted; external IDs are not necessarily available. |
| Assignments/gradable items and due dates | GET `/learn/api/public/v2/courses/{courseId}/gradebook/columns` | Stable column `id`, `name`, `contentId`, `grading.due`, `grading.type`, availability; not every column is an assignment. Join visible content metadata where available rather than labelling every grade column an assignment. Missing due dates remain missing. |
| Content metadata | GET `/learn/api/public/v1/courses/{courseId}/contents`; GET `/learn/api/public/v1/courses/{courseId}/contents/{contentId}`; GET `/learn/api/public/v1/courses/{courseId}/contents/{contentId}/children` | Titles/descriptions, content handler/type, hierarchy and links where exposed. The list is top-level only; inspect children for nested items. Availability/adaptive-release rules apply. No submission/file-download access is needed for this stage's design. |
| Course announcements | GET `/learn/api/public/v1/courses/{courseId}/announcements` | IDs, titles/bodies and availability metadata. Read published/student-visible announcements only; sanitize HTML before Discord presentation. |
| Institution announcements, optional | GET `/learn/api/public/v1/announcements` | System announcements, distinct from course announcements; separate permissions. Not needed for initial course-only sync. |
| Calendar and deadline representations | GET `/learn/api/public/v1/calendars/items` | `since`, `until`, optional `courseId` and `type`; IDs, title, start/end, calendar identity and item type. `type=GradebookColumn` represents gradable items; the calendar item ID can address its gradebook column. Calendar items also include course/personal/institutional events according to access. |

**Assignment coverage limits:** Ultra assignments/tests may be represented by visible grade columns plus content metadata and calendar deadline entries. Turnitin/LTI/external-tool details are not guaranteed to be complete in Learn REST. Manual grade columns also exist. Confirm provider types and student-specific deadline exceptions/accommodations with UWA before treating a common `grading.due` as the student's effective deadline. No grades, attempts, submissions or other students' records are required.

**Calendar limits:** Set an explicit time window. The current Swagger says a maximum 16-week span; older calendar guidance mentions 14 weeks, so use smaller bounded windows and confirm the deployed limit. Calendar guidance explicitly warns against unrestricted non-delegated calendar queries because they can expose institution-wide data. Use delegated student access, preferably course-filtered queries for enrolled courses. Blackboard calendar is not evidence of the MyCamu class timetable.

## Registration and UWA approval

**Confirmed:** Developer registration alone does not grant access to UWA. Create a REST application in the Anthology/Blackboard Developer Portal under a developer group, obtaining an Application ID and OAuth application key/secret. UWA's Learn administrator must install/register that Application ID under **Administrator Tools → Integrations → REST API Integrations → Create Integration**, choose the integration's Learn user/least-privilege role, and enable **End User Access** for delegated authorization.

Application ID (administrator installation identifier) and OAuth key (`client_id`) are different. Register the approved callback URL and keep the secret server-side. Do not reuse Blackboard's mobile app or another vendor's client registration. UWA must confirm its approval process, available integration slots/quotas and production/test access; public sources do not establish those tenant-specific details.

Keep the student consent workflow enabled; do not request the administrator's consent-bypass/Trusted Service options. Do not assign System Administrator or a broad institution-wide viewer role.

## OAuth and permissions

**Recommended supported method:** Three-legged OAuth 2.0 authorization-code flow, with S256 PKCE and validated random `state`. The student signs in manually through UWA's normal login/SSO/MFA and approves access in Blackboard. The bot never sees the UWA password or MFA values.

- Authorization: GET `/learn/api/public/v1/oauth2/authorizationcode`, with the application's OAuth key as `client_id`, approved `redirect_uri`, `response_type=code`, `scope`, `state`, and PKCE challenge/method.
- Token exchange: POST `/learn/api/public/v1/oauth2/token`, using the documented application authentication and authorization-code grant, matching redirect URI and PKCE verifier. Subsequent academic requests use the returned Bearer access token.
- Request **`read`**. Request **`offline`** in addition only with approved background synchronization/secure refresh-token storage. Do not request `write`, `delete` or wildcard access. Refresh grants/lifecycle must follow the official specification and UWA policy. No OAuth implementation or credential acquisition occurs in this investigation.
- OAuth scopes limit operations; Learn entitlements, enrolment and item visibility still determine which records are readable. A token is not authorization to access all students.

**Endpoint permissions confirmed by the specification:**

- Own memberships are readable without other-user enrolment permissions. Other-user access requires `system.user.course.enrollment.VIEW` and/or `system.user.org.enrollment.VIEW`; do not request these for this bot.
- Enrolled students can read the allowed course fields. Disabled-course/system-wide access permissions are unnecessary.
- Grade-column documentation mentions `course.gradebook.MODIFY` for privileged access, but explicitly permits enrolled students to read visible columns when the mygrade tool is available, including `grading.due`. Use this student route; do not grant MODIFY simply to read deadlines. Validate Ultra behavior on UWA's deployed version.
- Content visibility follows course/item availability for ordinary student users. Broad adaptive-release/edit entitlements are unnecessary and could reveal unavailable content.
- Published course announcements require `course.announcements.VIEW`. Viewing unavailable announcements also involves MODIFY; do not request that. Optional system announcements require `system.announcements.VIEW`, not the administrative variant.
- Calendar returns items viewable by the delegated user. Confirm student course/calendar access and the selected endpoints' entitlement checks with UWA; no calendar creation/edit permissions are needed.

**Alternative, not preferred:** Official two-legged `client_credentials` OAuth exists: POST to the same token endpoint with HTTP Basic application-key/secret authentication and form-encoded `grant_type=client_credentials`. It acts as the configured integration Learn user rather than the consenting student. This is unsuitable unless UWA can guarantee narrowly authorized student/course access server-side; never use it for an unrestricted calendar or course roster dump. No student password grant or browser-session replay is proposed.

## UWA India tenant and campus handling

**Confirmed public evidence:** [UWA India student glossary](https://india.uwa.edu.au/current-students/student-glossary) describes the LMS for assessments/materials/unit information; its public LMS links point to `https://lms.uwa.edu.au/`. [UWA learning-online guidance](https://www.uwa.edu.au/students/your-studies/learning-online) identifies Blackboard Ultra and links to the same host. This supports the same public LMS entry point for UWA India and UWA generally.

**Still unknown:** Actual Mumbai/Chennai enrolments and course visibility in that tenant, any distinct provisioning arrangements, and whether course metadata explicitly labels campuses. No standard campus field or UWA-specific mapping was confirmed. Do not infer campus from course name/code, term or hostname. Ask UWA for a supported mapping, including merged/shared courses; keep unknown distinct from both campuses.

## Exact request to UWA IT

Through [UWA India IT support](https://india.uwa.edu.au/current-students/it-support), request referral to the `lms.uwa.edu.au` Blackboard administrator and ask for:

1. Confirmation that Mumbai and Chennai students' units are on this Learn tenant, an approved consenting test student/test environment, and that UWA's SSO preserves the delegated OAuth callback through normal login/MFA. Anthology documents [custom SSO callback compatibility requirements](https://github.com/blackboard/anthologydevdocs/blob/main/docs/blackboard/rest-apis/getting-started/rest3LO-and-learnSSO.md); any configuration repair belongs to UWA administrators.
2. Approval/installation of a dedicated `uwa-india-discord-bot` REST Application ID, approved callback URI, End User Access enabled with normal consent retained, and a minimally privileged integration user/role.
3. Confirmation of `read` and, if background refresh is approved, `offline` authorization; key/secret provisioning through a secure channel, token refresh/revocation/rotation policy, and permitted credential storage. Do not send credentials in chat or commit them.
4. Confirmation that the listed APIs/version are available and student-delegated access exposes visible grade-column deadlines, announcements, content and calendar; sanitized examples for Ultra assignments, external tools and personal deadline exceptions.
5. Documented Mumbai/Chennai/shared-course mapping, quotas/rate limits, allowed polling frequency, and approval for local retention and Discord display restricted to the student/approved test audience.

## Stage 9 recommendation

**Technically feasible through official APIs and delegated OAuth; UWA access is not yet approved or validated.** Proceed with integration implementation only after UWA registration/access is confirmed and the user authorizes Stage 9. Preserve normalized source IDs, UTC timestamps and source links during future sync; keep Discord commands reading SQLite. This stage changes documentation only, adds no dependencies and leaves the Blackboard stub unchanged.

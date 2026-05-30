# Handoff: Creator License Risk Cards

  ## Current Situation
  - The original request was to add two new creator-license risk cards:
    1. **Delinquent creator-licensed users**
       - no observed consuming or creating activity in the last 6 months
       - show 10 at a time
       - paginated
       - include last observed activity date
    2. **Low creation share creator-licensed users**
       - creator-licensed users whose `developing_6m / viewing_6m < 5%`
       - example: `5 creates / 20 consumes` is fine, `5 / 100` is flagged
       - computed over Pulse’s fixed 6-month guidance window
  - I implemented these cards on the **wrong page**:
    - implemented on **Activity Overview**
    - user clarified they belong on **License Overview**

  ## Important Product Decisions Already Confirmed
  - These cards should live on **License Overview**
  - They should remain **fixed to creator-licensed users**
  - They should **not** change with the license-page `License Filter` dropdown
  - They **should** respond to the selected instance filter
  - The 6-month window is **Pulse guidance**, fixed and explicitly stated in the UI

  ## What Was Implemented
  ### Backend
  Added endpoint:
  - `python-lib/pulse_dashboard/webapp_backend/full_backend.py`
  - route: `/api/build/users/creator-risk`

  Behavior:
  - returns creator-license risk lists using current entitlement mapping
  - uses trailing fixed 6-month activity window
  - supports optional `instance_name`
  - supports separate paging params:
    - `delinquentPage`
    - `underutilizedPage`

  Response shape includes:
  - `meta`
    - `windowMonths`
    - `ratioThreshold`
    - `guidanceLabel`
  - `delinquentCreators`
    - `page`
    - `pageSize`
    - `totalRows`
    - `totalPages`
    - `rows`
  - `underutilizedCreators`
    - `page`
    - `pageSize`
    - `totalRows`
    - `totalPages`
    - `rows`

  Rules implemented:
  - **Delinquent**
    - current creator-licensed users
    - enabled users only
    - `viewing_6m = 0 AND developing_6m = 0`
  - **Low creation share**
    - current creator-licensed users
    - enabled users only
    - `viewing_6m > 0`
    - `(developing_6m / viewing_6m) < 0.05`

  Returned row fields include:
  - `instanceName`
  - `login`
  - `loginNorm`
  - `displayName`
  - `userProfile`
  - `viewing6m`
  - `developing6m`
  - `developingToViewingRatio` (underutilized only)
  - `lastActivityAt`

  ## What Was Implemented on Frontend
  External React source:
  - `/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/webapps/entry_point/frontend/src/App.js`

  Currently implemented:
  - creator-risk fetch/state/effects on `UsersActivityPage`
  - creator-risk cards rendered on **Activity Overview**
  - cards have:
    - pagination
    - count summary (`Showing X–Y of N`)
    - row click to set selected user
    - guidance copy
    - instance-aware fetch

  ## What Is Wrong Right Now
  - The cards are on **Activity Overview**
  - They should be moved to **License Overview**
  - The current session ended while back in plan mode, so the move was **not implemented**

  ## Latest Correct Plan
  ### Goal
  Move creator-license risk cards from `Activity Overview` to `License Overview`, keeping backend logic unchanged.

  ### Required frontend changes
  1. Remove from `UsersActivityPage`:
     - creator-risk state
     - creator-risk fetch effect
     - creator-risk card section
  2. Add to `UsersLicensePage` / `LicenseOverviewSection`:
     - creator-risk state
     - creator-risk fetch effect
     - card rendering section
  3. Keep these cards **independent of the license filter dropdown**
  4. Keep them **scoped by selected instance**
  5. Keep independent pagination for both cards
  6. Keep row click behavior if practical on license page
     - if license page does not already own user detail modal state, add minimal state needed

  ### Placement on license page
  Recommended placement:
  - inside License Overview
  - below entitlement summary and instance-specific entitlement subsection
  - still clearly labeled as a fixed 6-month Pulse guidance signal

  ## Validation That Was Already Done
  - Backend compiled successfully with:
    - `python -m py_compile python-lib/pulse_dashboard/webapp_backend/full_backend.py`
  - Frontend built successfully
  - Served assets were synced into:
    - `resource/pulse-dashboard/build`

  ## Relevant Files
  - Backend endpoint:
    - `python-lib/pulse_dashboard/webapp_backend/full_backend.py`
  - Frontend source:
    - `/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/webapps/entry_point/frontend/src/App.js`
  - Served packaged assets:
    - `resource/pulse-dashboard/build`

  ## Recommended Next Prompt For New Session
  Use something like:

  > Move the creator-license risk cards from Activity Overview to License Overview. Keep the existing `/api/build/users/creator-risk` backend endpoint and logic
  unchanged. The cards should stay fixed to creator-licensed users regardless of the License Filter dropdown, but should respond to the selected instance filter.
  Remove the cards and their fetch/state from `UsersActivityPage`, add them to `UsersLicensePage` / `LicenseOverviewSection`, rebuild the external frontend, and
  sync `resource/pulse-dashboard/build`.

  ## Note
  - There was also prior successful work on the License Overview entitlement redesign before this feature work.
  - The main issue for this handoff is specifically:
    - **backend is present**
    - **frontend placement is wrong**
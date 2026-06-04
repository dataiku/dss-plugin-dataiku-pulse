# Load Event Server History — Development Prompt Series

This document breaks the work into a sequence of numbered prompts for later implementation.
The prompts are grouped into three phases:
1. Macro interface and user interaction
2. Path discovery and input validation
3. Implementation and processing

Use these prompts in order. Each prompt is intended to be specific enough to hand to an implementation agent without requiring extra framing.

## Phase 1 — Macro Interface and User Interaction

1. **Define the runnable interface for history import**
   - Update the `load-event-server-history` runnable definition so it is designed for historical event-server imports.
   - The interface should ask the user for:
     - a managed folder id
     - whether to import a single node or all discovered nodes
     - an optional start date
     - an optional end date
   - Keep the runnable aligned with existing Pulse runnable conventions, including use of the `pulse_primary` preset.
   - Do not implement the parsing/import logic yet; only define the user-facing parameter model and runnable metadata.

2. **Add node selection UX behavior to the runnable design**
   - Extend the runnable parameter design so the user can choose between:
     - importing one discovered node
     - importing all discovered nodes
   - When the user chooses a single node, the interface should expose a dropdown of discovered node values.
   - When the user chooses all nodes, the single-node selector should be ignored or hidden.
   - Keep the UX simple and explicit so users understand that importing all nodes may take significantly longer.

3. **Add date-range UX behavior to the runnable design**
   - Add optional `start_date` and `end_date` parameters to the runnable interface.
   - Blank dates should mean “full available history” for the selected scope.
   - The design should clearly support these cases:
     - no dates provided
     - only start date provided
     - both start and end dates provided
   - Reject or validate invalid ranges later in runnable logic, but reflect the intended behavior in the interface design now.

4. **Design the resource hook contract for interactive parameter population**
   - Plan the custom resource hook(s) needed so the macro can react to the folder id input and populate node choices dynamically.
   - The resource behavior should be designed to:
     - accept the managed folder id
     - inspect the expected history root structure
     - discover available node directories
     - return sorted dropdown choices for the single-node selection field
   - Keep this prompt focused on interface and contract design, not on fully implementing file traversal yet.

## Phase 2 — Path Discovery and Input Validation

5. **Implement managed folder discovery for the provided folder id**
   - Build the folder-opening logic for the history import runnable using the folder id provided by the user.
   - Use the DSS/Dataiku folder APIs to resolve the folder handle safely.
   - The implementation should verify that the folder is accessible before any import work begins.
   - Produce clear errors for invalid folder ids, inaccessible folders, or missing permissions.

6. **Discover the expected event-server history root structure**
   - Implement logic to inspect the managed folder contents and verify that the expected `generic/` root exists.
   - Under that root, discover the available per-node directories.
   - Treat those node directories as the valid import candidates.
   - Return a sorted, stable list of discovered node identifiers for downstream selection logic.

7. **Support single-node and all-node resolution**
   - Implement the logic that converts the user’s selection into the final set of node directories to import.
   - If the user selected a single node, validate that it still exists in the folder at runtime.
   - If the user selected all nodes, resolve all discovered node directories.
   - Fail clearly if no valid nodes are found.

8. **Discover all candidate history file paths for the selected import scope**
   - Implement recursive discovery of candidate history files beneath the resolved node directory or directories.
   - Find all relevant `.gz` files that could contain audit log history.
   - Capture enough metadata for each file to support filtering, logging, and execution summaries.
   - Keep the discovery logic separate from parsing logic so it can be tested independently.

9. **Apply date-range filtering to discovered history files**
   - Implement filtering logic for the discovered file paths based on the optional start/end date inputs.
   - Support:
     - full-history import when both dates are blank
     - lower-bound filtering when only start date is given
     - bounded filtering when both dates are given
   - Reject invalid ranges such as end date before start date.
   - If no files remain after filtering, return a clear result instead of silently succeeding.

10. **Define and implement discovery-stage execution summaries**
    - Add summary reporting for the discovery phase before parsing begins.
    - Include at least:
      - selected folder id
      - import mode (single node vs all nodes)
      - resolved node count
      - candidate file count
      - filtered file count
      - requested date range
    - This summary should make it easy to confirm that the runnable is about to process the intended scope.

## Phase 3 — Actual Development and Processing Logic

11. **Reuse the existing audit-log parsing pipeline for history imports**
    - Implement the history runnable so it reuses the same parsing and normalization behavior as `data-gather-audit-logs` wherever practical.
    - The history loader should read matching `.gz` files, parse the JSON log lines, normalize timestamps, flatten message content, and prepare records for audit processors.
    - Do not create a parallel parsing standard if existing live-audit logic can be reused safely.

12. **Process history files in a memory-safe streaming/chunked manner**
    - Implement file reading so large history imports do not require loading the full dataset into memory at once.
    - Read and process files incrementally in chunks.
    - Handle corrupt, empty, or malformed files robustly, with explicit logging and summary counts.
    - Keep the processing design safe for large “all nodes” imports.

13. **Run audit processors and write silver output only**
    - For parsed history records, run the same audit processors used by the live audit-log gatherer.
    - Normalize the processor outputs into silver format and run the same silver data-quality checks.
    - Write only silver parquet outputs to the standard Pulse output target.
    - Do not write raw backup payloads.
    - Do not trigger gold refresh work as part of this runnable.

14. **Keep historical import state isolated from incremental live-audit state**
    - Ensure the history import runnable does not update or interfere with the incremental audit cursor used by the live audit collector.
    - Historical backfill should be operationally separate from the existing incremental audit ingestion flow.
    - Document this behavior clearly in code-level comments or result summaries if needed.

15. **Add final result reporting for the full import run**
    - Return a clear end-of-run summary covering:
      - folder id
      - selected node or node count
      - date range
      - files scanned
      - files skipped/failed
      - chunks processed
      - rows read
      - rows written to silver
      - processor failures
    - Make the result useful for users running large historical imports so they can confirm the run scope and outcome.

16. **Validate the runnable with focused test scenarios**
    - Add or run focused validation for the following scenarios:
      - valid folder with multiple nodes
      - missing `generic/` root
      - invalid folder id
      - single-node import
      - all-node import
      - full-history import
      - bounded date-range import
      - no files matched after filtering
      - malformed `.gz` file handling
      - successful silver-only output generation
    - Keep validation focused on this runnable’s behavior and avoid unrelated refactors.

## Defaults to Preserve During Implementation

17. **Preserve agreed defaults during development**
    - Assume the managed folder contains a `generic/` root with node directories beneath it.
    - Keep date inputs optional.
    - Support both single-node and all-node imports.
    - Default to silver-only writes.
    - Do not add special deduplication state unless requirements change later.

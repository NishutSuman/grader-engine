# CodeJudge SQL & DBMS Dataset Dictionary
This package contains raw CSV exports from a coding-practice and evaluation platform. The data is intentionally realistic and may contain quality issues that should be detected through SQL audits.
## Tables
### `batches.csv`
Batch/master cohort data. One row per academic batch. batch_id is the intended identifier; batch_code is a readable code.

- Rows: `6`
- Columns:
  - `batch_id`
  - `batch_code`
  - `program`
  - `start_date`
  - `end_date`
  - `batch_status`

### `courses.csv`
Course catalog data. course_id is the intended identifier; course_code is expected to be unique in a clean design.

- Rows: `10`
- Columns:
  - `course_id`
  - `course_code`
  - `course_title`
  - `course_status`
  - `credit_hours`

### `students.csv`
Student master data. student_id is the intended identifier; roll_number and email are likely candidate keys.

- Rows: `320`
- Columns:
  - `student_id`
  - `roll_number`
  - `full_name`
  - `email`
  - `batch_id`
  - `admission_date`
  - `enrollment_status`
  - `graduation_year`

### `enrollments.csv`
Student-course enrollment records. enrollment_id is a raw row identifier; student_id + course_id is an important composite uniqueness candidate.

- Rows: `719`
- Columns:
  - `enrollment_id`
  - `student_id`
  - `course_id`
  - `enrolled_on`
  - `enrollment_status`
  - `final_grade`

### `problems.csv`
Programming problems mapped to courses. problem_id is the intended identifier; problem_code is expected to be unique.

- Rows: `67`
- Columns:
  - `problem_id`
  - `course_id`
  - `problem_code`
  - `title`
  - `difficulty`
  - `max_score`
  - `created_at`
  - `is_active`

### `test_cases.csv`
Test cases for programming problems. test_case_id is the intended identifier; problem_id + case_no should identify a test case within a problem.

- Rows: `330`
- Columns:
  - `test_case_id`
  - `problem_id`
  - `case_no`
  - `input_label`
  - `expected_output_label`
  - `points`
  - `is_hidden`

### `contests.csv`
Coding contests/evaluations linked to courses. contest_id is the intended identifier.

- Rows: `12`
- Columns:
  - `contest_id`
  - `course_id`
  - `contest_title`
  - `start_time`
  - `end_time`
  - `contest_status`

### `contest_problems.csv`
Mapping table between contests and problems. contest_id + problem_id is the intended composite key.

- Rows: `63`
- Columns:
  - `contest_id`
  - `problem_id`
  - `problem_order`

### `submissions.csv`
Student code submissions against problems and optionally contests. submission_id is the intended identifier.

- Rows: `2501`
- Columns:
  - `submission_id`
  - `student_id`
  - `problem_id`
  - `contest_id`
  - `language`
  - `submitted_at`
  - `status`
  - `score`
  - `runtime_ms`

### `test_results.csv`
Result of running individual test cases for submissions. result_id is the raw identifier; submission_id + test_case_id should usually be unique.

- Rows: `9673`
- Columns:
  - `result_id`
  - `submission_id`
  - `test_case_id`
  - `result_status`
  - `runtime_ms`
  - `memory_kb`
  - `awarded_points`

### `sessions.csv`
Course sessions such as lectures, tutorials, or labs.

- Rows: `48`
- Columns:
  - `session_id`
  - `course_id`
  - `session_title`
  - `session_date`
  - `session_type`

### `attendance.csv`
Student attendance for sessions. attendance_id is the raw identifier; session_id + student_id should usually be unique.

- Rows: `2352`
- Columns:
  - `attendance_id`
  - `session_id`
  - `student_id`
  - `attendance_status`
  - `marked_at`

### `regrade_requests.csv`
Requests raised by students for re-evaluating submissions.

- Rows: `80`
- Columns:
  - `request_id`
  - `submission_id`
  - `student_id`
  - `requested_at`
  - `reason`
  - `request_status`
  - `resolved_at`

### `plagiarism_flags.csv`
Similarity/plagiarism review flags between submissions.

- Rows: `60`
- Columns:
  - `flag_id`
  - `submission_id`
  - `matched_submission_id`
  - `similarity_score`
  - `flag_status`
  - `created_at`

### `raw_student_import.csv`
Raw staging import of new students. This table is intentionally staging-like and may contain messy records.

- Rows: `80`
- Columns:
  - `raw_row_id`
  - `roll_number`
  - `full_name`
  - `email`
  - `batch_code`
  - `admission_date`
  - `import_status`
  - `import_notes`

### `operation_requests.csv`
Administrative data-change requests used for safe update/delete and transaction exercises.

- Rows: `35`
- Columns:
  - `operation_id`
  - `requested_by`
  - `operation_type`
  - `target_table`
  - `target_record_id`
  - `requested_at`
  - `reason`
  - `approval_status`
  - `executed_at`


# Grading Report: iitmcs_24093225

---
## Part 1 — Relational Design, Keys & Normalization (30 marks)

**Repository:** https://github.com/adityabagad0409/codejudge-part1-db-design
**Files Found:** README.md, assumptions.md, erd.md, keys_and_relationships.md, normalization_notes.md, schema.sql, schema_explanation.md
**Score: 27 / 30**

### Score Breakdown

| Component | Score | Feedback |
|---|---|---|
| Raw data and schema understanding | 5/5 | schema_explanation.md clearly describes all 16 tables including batches, courses, students, enrollments, problems, test_cases, contests, contest_problems, submissions, test_results, sessions, attendance, regrade_requests, plagiarism_flags, raw_student_import, and operation_requests. Each explanation correctly identifies primary identifiers, relationships, and domain constraints. |
| Entity and relationship identification | 5/6 | The ERD in erd.md correctly identifies one-to-many relationships (e.g., batches→students, courses→problems, students→submissions) and many-to-many relationships (contests↔problems via contest_problems). However, the ERD is incomplete—contest_problems is shown as a bridge in the relationship list but not all junction tables are fully detailed in the diagram syntax, and the plagiarism_flags self-relationship is not clearly illustrated. The relationships summary in keys_and_relationships.md compensates somewhat. |
| Primary key, foreign key, candidate key, alternate key, composite key reasoning | 6/6 | keys_and_relationships.md provides a comprehensive table identifying primary keys (e.g., batch_id, course_id, submission_id), candidate/alternate keys (e.g., batch_name, email, course_code), composite keys (e.g., (student_id, course_id) in enrollments, (contest_id, problem_id)), and foreign key references with complete reasoning. The schema.sql enforces all these through PRIMARY KEY, UNIQUE, and FOREIGN KEY constraints with clear CHECK conditions. |
| Normalization reasoning | 5/5 | normalization_notes.md provides concrete redundancy examples (batch names, course names, problem metadata), explains functional dependencies, demonstrates partial dependency removal (e.g., contest_title depending only on contest_id), and justifies 1NF/2NF/3NF compliance. The separation of bridge tables (contest_problems, test_results, attendance) is well-motivated. Trade-offs are explicitly acknowledged (status values using CHECK instead of separate lookup tables). |
| SQL DDL schema quality | 4/5 | schema.sql is well-structured with PRAGMA foreign_keys enabled, proper DROP statements, atomic columns, and comprehensive CHECK constraints (status values, date ordering, positive scores, language enumerations). However, the file is truncated mid-definition of plagiarism_flags table (ends at 'created_a'), making the schema incomplete and unrunnable. This is a critical error despite otherwise high-quality DDL design. |
| ERD / relationship diagram clarity | 2/3 | The Mermaid ERD in erd.md uses correct syntax and shows primary relationships clearly, but has two significant limitations: (1) it does not include all tables (raw_student_import, operation_requests are missing), and (2) the self-relationship in plagiarism_flags (matched_submission_id) is not represented. A more complete diagram would strengthen the visualization. |

### Feedback

The submission demonstrates strong conceptual understanding of relational design, normalization, and database constraints with well-articulated explanations across multiple documents. The assumptions.md provides valuable context, and the normalization reasoning is thorough with concrete examples. However, the submission has two critical issues that significantly impact the grade: (1) the schema.sql file is truncated and incomplete, preventing it from being executed, and (2) the ERD omits several tables and does not capture all relationships, reducing its utility. To improve, the student must complete the schema.sql file (especially plagiarism_flags and any other incomplete tables), include all 16 entities in the ERD, and ensure all files are syntactically valid before submission.

---
## Part 2 — SQL Query Implementation & Verification (20 marks)

**Repository:** https://github.com/adityabagad0409/codejudge-part2-sql-queries
**Files Found:** README.md, queries.sql, query_outputs.md, sql_reasoning.md
**Score: 14 / 20**

### Score Breakdown

| Component | Score | Feedback |
|---|---|---|
| Correct SQL queries (20 queries) | 6/8 | Only 13 complete queries are present in queries.sql; Query 13 is truncated at `s.student_` and queries 14–20 are missing entirely. Of the 13 present queries, most syntax appears correct (Queries 1–12), but the incomplete submission means 7 queries out of 20 are not deliverable. This is a significant shortfall in meeting the assignment requirement of 20 queries. |
| Joins, aggregation, and subquery correctness | 4/5 | Queries 6, 7, 8, and 9 demonstrate correct multi-table JOINs with proper ON clauses. Query 8 correctly uses LEFT JOIN with GROUP BY and aggregate COUNT(DISTINCT). However, the absence of queries 14–20 and the truncation of Query 13 prevent full evaluation of subquery patterns (Query 16 in sql_reasoning.md references a subquery that is not shown in the actual implementation). The reasoning document shows understanding but the code does not fully back it up. |
| Output documentation and validation notes | 1/3 | query_outputs.md is entirely a template with all entries marked 'Pending dataset execution' and generic validation notes. No actual query outputs, result samples, row counts, or concrete validation evidence are provided. The student was expected to run the queries and document real results; this file demonstrates no execution was performed or documented. |
| SQL reasoning explanations (5 explanation questions) | 2/3 | sql_reasoning.md addresses 4 of 5 expected topics: LEFT JOIN vs INNER JOIN (Query 7, sound reasoning), HAVING vs WHERE (Query 13, correct but query incomplete), Subquery use (Query 16, explained but not shown in code), and Edge cases (Query 14, not present in code). The explanations are conceptually sound but lack grounding in working, complete code examples. The missing Query 14 and incomplete Query 13 weaken the credibility of these explanations. |
| Code organization and readability | 1/1 | The queries.sql file is well-organized with clear comment headers for each query, proper indentation, and logical structure. However, organization cannot compensate for incomplete submission. |

### Feedback

This submission shows good conceptual understanding of SQL joins, aggregation, and reasoning about query design, as evidenced by the sound explanations in sql_reasoning.md and the quality of the 13 complete queries. However, the assignment has critical delivery failures: Query 13 is truncated mid-execution, queries 14–20 are entirely missing (meaning only 13 of 20 required queries are present), and query_outputs.md contains no actual results or validation—only a template. The README is clear and helpful, but the core deliverables are incomplete. The student must complete all 20 queries, execute them against actual data, document concrete outputs with row samples and validation evidence, and ensure all files are properly submitted and tested before resubmission.

---
## Part 3 — Data Integrity Audit, Debugging & Repair Plan (25 marks)

**Repository:** https://github.com/adityabagad0409/codejudge-part3-integrity-audit
**Files Found:** README.md, before_after_evidence.md, domain_rule_checks.sql, import_validation.sql, integrity_audit.sql, repair_plan.md, staging_repair_scripts.sql
**Score: 19 / 25**

### Score Breakdown

| Component | Score | Feedback |
|---|---|---|
| Import validation and row-count checks | 3/3 | import_validation.sql is comprehensive and well-structured. It includes row-count checks across all 15 tables, distinct primary key counts, NULL/blank checks for critical columns, and empty-table validation. All required validation queries are present and correctly formatted for SQLite. |
| Primary key and uniqueness audit | 4/4 | integrity_audit.sql properly audits all primary keys (students, submissions, test_results) for duplicates using GROUP BY HAVING clauses. Candidate key checks for email, enrollments, contest_problems, test_results, and attendance are all included. Foreign key checks are extensive and well-organized. |
| Foreign key and relationship audit | 5/5 | The foreign key audit in integrity_audit.sql is thorough and covers all major relationships: students→batches, enrollments→students/courses, problems→courses, test_cases→problems, contests→courses, submissions→students/problems/contests, test_results→submissions/test_cases, sessions→courses, attendance→sessions/students, regrade_requests→submissions/students, and plagiarism_flags→submissions. All LEFT JOIN patterns are correct. |
| Domain and rule validation | 4/4 | domain_rule_checks.sql includes comprehensive checks: score ranges (0-100), difficulty levels (Easy/Medium/Hard), submission statuses (6 valid values), programming languages (6 types), test-result statuses, attendance statuses, contest statuses, operation-request statuses, and temporal constraints (end_time vs start_time, resolved_at vs requested_at, etc.). One minor issue: the email validation in domain_rule_checks.sql uses a simple LIKE pattern '%@%.%' which may miss some edge cases, but this is reasonable for domain checking. |
| Repair plan with dataset-specific examples | 1/5 | repair_plan.md is a template with all entries marked 'Pending dataset execution' and 'Actual ID required'. The file provides good decision rules and a clear structure, but the assignment explicitly requires 'at least 8 specific examples using actual record IDs' with actual data. The student has not executed the audit queries against their dataset or populated the table with real findings. This is a critical gap—the entire purpose of Part 3 is to identify real issues in the imported data and demonstrate concrete repairs. |
| Safe staging repair scripts with before/after evidence | 2/4 | staging_repair_scripts.sql correctly creates staging tables and implements five repairs (invalid emails, duplicate enrollments, negative scores, excessive scores, orphan test results) with proper BEFORE/AFTER query patterns. However, before_after_evidence.md is also entirely unpopulated ('Pending dataset execution') with no actual query results, row counts, or evidence. The student created the repair logic but did not execute it and capture evidence, which defeats the verification requirement. |

### Feedback

This submission demonstrates strong understanding of SQL audit techniques and proper safety practices (staging tables, before/after patterns, foreign key logic). The audit queries in import_validation.sql, integrity_audit.sql, and domain_rule_checks.sql are well-written and comprehensive. However, the submission is fundamentally incomplete: the student has built the framework but failed to execute the audit against an actual dataset, identify real issues with specific record IDs, and provide before/after evidence of repairs. The repair_plan.md and before_after_evidence.md files are templates only, with all rows marked 'Pending.' To achieve full marks, the student must run the audit queries on their dataset, populate repair_plan.md with 8+ real issues (with actual student_id, submission_id, etc.), execute staging_repair_scripts.sql, and document actual before/after row counts and record examples. This is a template submission, not a completed audit.

---
## Part 4 — Transactions, Safe Changes & DB Reliability (25 marks)

**Repository:** https://github.com/adityabagad0409/codejudge-part4-transactions-reliability
**Files Found:** README.md, acid_explanation.md, incident_note.md, safe_deletes.sql, safe_updates.sql, transactions.sql
**Score: 25 / 25**

### Score Breakdown

| Component | Score | Feedback |
|---|---|---|
| Safe UPDATE scripts with validation (≥4 UPDATEs with before/after) | 5/5 | Five UPDATE operations provided in safe_updates.sql, each with clear before/after validation queries. All use targeted WHERE clauses (invalid emails, negative scores, scores >100, full-score pending submissions, validated operations). The staging table approach is sound and demonstrates proper safety practice. |
| Safe DELETE scripts with validation (≥2 DELETEs with justification) | 3/3 | Two DELETE operations in safe_deletes.sql with clear justification. DELETE 1 removes duplicates using MIN(rowid), and DELETE 2 removes orphan test results after archiving them to rejected_test_results. Both include before/after validation queries demonstrating the safety approach. |
| Transaction scenarios (≥3 scenarios) | 7/7 | Four transaction scenarios provided in transactions.sql, exceeding the minimum of 3. Scenarios cover: (1) successful submission+test result insert with COMMIT, (2) invalid enrollment with ROLLBACK, (3) score correction with SAVEPOINT and partial rollback, and (4) regrade request resolution with coordinated updates. All scenarios are realistic and well-structured. |
| Correct use of COMMIT, ROLLBACK, and/or SAVEPOINT | 3/3 | All three transaction control mechanisms are correctly used: COMMIT in Scenarios 1 and 4, ROLLBACK in Scenario 2, and SAVEPOINT with ROLLBACK TO in Scenario 3. PRAGMA foreign_keys is enabled for safety. The usage is contextually appropriate in each scenario. |
| ACID explanation using own example | 4/4 | acid_explanation.md provides a clear ACID breakdown using Scenario 2 (invalid enrollment rollback). Atomicity, Consistency, Isolation, and Durability are each explained with specific reference to the enrollment example. The explanation correctly notes that durability does not apply since the transaction was rolled back. |
| Reliability incident note | 3/3 | incident_note.md describes a realistic incident (UPDATE without WHERE clause on submissions table). It covers impact (rankings, reports, status integrity), detection method (COUNT query), recovery options (ROLLBACK, backup restore, audit log), and prevention strategies (SELECT before UPDATE, transactions, code review, access control, backups). Well-reasoned and practical. |

### Feedback

This is a strong submission that demonstrates solid understanding of database transactions and safe modification practices. Strengths include proper use of staging tables, comprehensive before/after validation in all UPDATE and DELETE operations, correct implementation of COMMIT/ROLLBACK/SAVEPOINT mechanics, and thoughtful ACID and incident analysis. The four transaction scenarios are realistic and well-documented. Minor areas for enhancement: the assignment requests ≥4 UPDATEs (5 were provided, which is good), and the incident note is excellent but could have included an example of detecting partial/corrupted recovery scenarios. Overall, this work shows professional-level database reliability thinking.

---
## Overall Score: 85 / 100

**Percentage: 85.0%**

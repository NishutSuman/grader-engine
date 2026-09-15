# Q1. Introduction — SQL & DBMS Assignment: CodeJudge Database System

## Context

CodeJudge is an online coding-practice and evaluation platform used by a Computer Science department. The platform stores data about students, batches, courses, programming problems, test cases, coding contests, code submissions, test results, attendance, regrade requests, plagiarism flags, and administrative operation requests.

In this assignment, you will work with relational database-style data and perform tasks related to database design, schema creation, SQL querying, integrity validation, safe data modification, and transaction handling.

This assignment is divided into four graded parts. Each graded part must be submitted separately as a public GitHub repository link.

---

## Dataset

Download the student dataset package from the link below:

**Dataset Link:** https://drive.google.com/drive/folders/1I2KsaOmxG3dE0Gk2f705PTOLVE8N1CXm?usp=sharing

The package contains raw CSV exports from the CodeJudge platform.

You are expected to inspect the files, understand relationships, create a relational schema, import the data, write SQL queries, identify integrity issues, and reason about safe database operations.

---

## Dataset Files

The dataset package contains the following files:

```text
README_DATASET.md
DATA_DICTIONARY.md
load_sqlite_raw.py
data/
```

The `data/` folder contains:

```text
batches.csv
courses.csv
students.csv
enrollments.csv
problems.csv
test_cases.csv
contests.csv
contest_problems.csv
submissions.csv
test_results.csv
sessions.csv
attendance.csv
regrade_requests.csv
plagiarism_flags.csv
raw_student_import.csv
operation_requests.csv
```

---

## Platform Submission Flow

There are five questions visible on the platform:

| Platform Question | Purpose | Submission Required |
|---|---|---|
| Q1 | Introduction and guidelines | No submission required. Click `Next`. |
| Q2 | Part 1 — Relational Design, Keys & Normalization | Submit one public GitHub repository link. |
| Q3 | Part 2 — SQL Query Implementation & Verification | Submit one public GitHub repository link. |
| Q4 | Part 3 — Data Integrity Audit, Debugging & Repair Plan | Submit one public GitHub repository link. |
| Q5 | Part 4 — Transactions, Safe Changes & DB Reliability | Submit one public GitHub repository link. |

For Q2, Q3, Q4, and Q5:

- Submit only one public GitHub repository link.
- Do not submit explanations, screenshots, extra text, or multiple links in the answer box.
- The GitHub repository must be public.
- The submitted repository must contain all required files for that part.
- The repository should be runnable/evaluable without private credentials or local-only paths.
- If a repository is private, empty, inaccessible, or contains the wrong files, marks may be deducted heavily.

---

## Recommended Tools

You may use any relational database system such as:

- MySQL
- PostgreSQL
- SQLite

You may also use supporting tools such as:

- DB Browser for SQLite
- MySQL Workbench
- pgAdmin
- VS Code

Your final submission should primarily contain SQL files and Markdown documentation.

---

## Optional SQLite Raw Loader

The dataset package contains a helper script:

```bash
python load_sqlite_raw.py
```

This script creates a raw SQLite database and imports the CSV files into flexible raw tables. This is only a convenience loader. You are still expected to design your own relational schema as required by the assignment.

---

## Overall Marks

| Part | Title | Marks |
|---|---|---:|
| Part 1 | Relational Design, Keys & Normalization | 30 |
| Part 2 | SQL Query Implementation & Verification | 20 |
| Part 3 | Data Integrity Audit, Debugging & Repair Plan | 25 |
| Part 4 | Transactions, Safe Changes & DB Reliability | 25 |
| **Total** |  | **100** |

---

## General Evaluation Expectations

Your work will be evaluated on:

- correctness of SQL syntax
- correctness of relational design
- understanding of primary keys, foreign keys, candidate keys, and composite keys
- appropriate use of constraints
- ability to identify and explain data integrity issues
- practical SQL querying ability
- ability to validate query outputs
- safe handling of UPDATE and DELETE operations
- transaction control and ACID reasoning
- clarity of explanation in Markdown files
- reproducibility of your repository

AI tools may be used for support, but generic answers that are not grounded in the given dataset will receive low marks.

Wherever explanation is required, use actual table names, column names, records, and examples from the provided dataset.

---

## Important Data Handling Rules

- Do not edit the original CSV files directly.
- If you perform corrections, use staging tables, copied tables, or database copies.
- For repair and transaction tasks, show before-and-after evidence.
- If you make assumptions, state them clearly in the relevant Markdown file.
- Use actual record IDs from the dataset wherever you discuss data quality or repairs.


---

# Q2. Part 1 — Relational Design, Keys & Normalization [30 Marks]

## Objective

In this part, you will understand the raw CodeJudge dataset and convert it into a well-structured relational database design.

This part tests your understanding of DBMS fundamentals, relational models, entities, relationships, keys, constraints, and normalization.

---

## Tasks

### Task 1: Understand the Raw Data

Inspect all CSV files provided in the dataset package.

Create a schema understanding document explaining:

- what each file/table represents
- what each important column means
- which columns can identify records
- which columns can be used to connect tables
- where the data appears repeated, dependent, or not normalized

You should not simply copy the data dictionary. You must explain the schema in your own words using the CodeJudge context.

---

### Task 2: Identify Entities and Relationships

Identify the main entities in the system.

Examples may include:

- students
- batches
- courses
- enrollments
- problems
- test cases
- contests
- contest-problem mappings
- submissions
- test results
- sessions
- attendance
- regrade requests
- plagiarism flags
- raw imports or operation requests

For each entity, explain:

- why it should be a separate table
- what its primary key should be
- what foreign keys it should contain
- whether any composite key is required
- which columns should be unique
- which columns should not allow NULL values

---

### Task 3: Identify Keys and Constraints

For the main tables, identify and justify:

- primary keys
- candidate keys
- alternate keys
- foreign keys
- composite keys
- NOT NULL constraints
- UNIQUE constraints
- CHECK constraints

Use actual column names from the dataset.

Your answer should explain why each key or constraint is needed from a DBMS perspective.

---

### Task 4: Normalization Reasoning

Analyze the raw data for possible redundancy or design issues.

Your answer should cover:

- at least 3 examples of repeated or redundant data
- at least 2 examples where separating data into another table improves design
- at least 2 examples of functional dependency or partial dependency, wherever applicable
- whether your final design is approximately in 1NF, 2NF, and 3NF
- any trade-offs you are making in the design

You do not need to over-normalize every field, but your design decisions must be justified.

---

### Task 5: Create SQL Schema

Write SQL DDL statements to create the database tables.

Your schema should include:

- suitable table names
- suitable column names
- appropriate data types
- primary key constraints
- foreign key constraints wherever applicable
- NOT NULL constraints where required
- UNIQUE constraints where logically needed
- CHECK constraints where suitable

You may make reasonable assumptions if the raw CSV does not explicitly enforce all constraints, but your assumptions must be explained.

Important: Since the raw CSVs may contain inconsistent records, you may choose to first import into raw/staging tables and then design a cleaner relational schema.

---

### Task 6: ERD or Relationship Diagram

Create a simple ERD or relationship diagram.

You may create it using any tool, or you may write it in text format using Markdown.

The diagram must clearly show:

- tables
- primary keys
- foreign keys
- one-to-many relationships
- many-to-many relationships through mapping tables

---

## Required Repository Files

Your Part 1 GitHub repository must contain:

```text
README.md
schema.sql
schema_explanation.md
keys_and_relationships.md
normalization_notes.md
erd.png or erd.md
assumptions.md
```

---

## Marks Breakdown

| Component | Marks |
|---|---:|
| Raw data and schema understanding | 5 |
| Entity and relationship identification | 6 |
| Primary key, foreign key, candidate key, alternate key, and composite key reasoning | 6 |
| Normalization reasoning | 5 |
| SQL DDL schema quality | 5 |
| ERD / relationship diagram clarity | 3 |
| **Total** | **30** |

---

## Submission Instruction

Submit only the public GitHub repository link for Part 1.

Do not submit any extra text.


---

# Q3. Part 2 — SQL Query Implementation & Verification [20 Marks]

## Objective

In this part, you will write selected SQL queries on top of your database and verify that the outputs are logically correct.

This part tests practical SQL querying ability, but marks are awarded not only for writing queries, but also for validating outputs using the actual dataset.

---

## Tasks

Write SQL queries for the problem statements below.

Each query should be saved in a `.sql` file. You may keep all queries in one file named `queries.sql`.

For each query, include:

- a comment explaining the purpose of the query
- the SQL query
- a small sample output or result summary
- one short validation note explaining why the output makes sense

---

## Query Set

### Basic Retrieval and Filtering

1. List all active students with student ID, name, email, batch, and admission date.
2. Find students whose email is missing or appears invalid.
3. List all problems with difficulty level `Easy` or `Medium`.
4. Display the latest 20 submissions based on submission timestamp.
5. Find submissions where the status is not successful.

### Joins

6. Display each submission with student name, problem title, language, status, score, and submitted time.
7. Display all students and their enrollments, including students who are not enrolled in any course.
8. Display all courses with the number of enrolled students.
9. Display test-case results for each submission, including problem title and student name.
10. Find students who are enrolled in a course but have not submitted any solution for that course.

### Aggregation and HAVING

11. Count submissions by status.
12. Calculate average score per problem.
13. Find students with more than a chosen number of submissions.
14. Find problems where the success rate is below 40%.
15. Find the top 10 most attempted problems.

### Subqueries / Set Logic

16. Find students whose average score is greater than the overall average score.
17. Find problems that have never been attempted.
18. Find students who have enrolled but never submitted any solution.
19. Find students who submitted solutions in both `Python` and `Java`.
20. Find the second-highest score for a selected problem.

---

## Explanation Questions

Answer the following in Markdown using examples from your own queries:

1. Explain one query where using `LEFT JOIN` is more appropriate than `INNER JOIN`.
2. Explain one query where `HAVING` is required instead of `WHERE`.
3. Explain one query where a subquery helped solve the problem.
4. Explain one situation where your query output could be misleading if duplicate records exist.
5. Explain one edge case you considered while writing any query.

---

## Required Repository Files

Your Part 2 GitHub repository must contain:

```text
README.md
queries.sql
query_outputs.md
sql_reasoning.md
```

---

## Output Expectations

For each query:

- write the SQL query
- mention the purpose
- provide either a small sample output or a short result summary
- provide a validation note based on the actual dataset

The evaluator should be able to run your queries on the imported database.

---

## Marks Breakdown

| Component | Marks |
|---|---:|
| Correct SQL queries | 8 |
| Joins, aggregation, and subquery correctness | 5 |
| Output documentation and validation notes | 3 |
| SQL reasoning explanations | 3 |
| Code organization and readability | 1 |
| **Total** | **20** |

---

## Submission Instruction

Submit only the public GitHub repository link for Part 2.

Do not submit any extra text.


---

# Q4. Part 3 — Data Integrity Audit, Debugging & Repair Plan [25 Marks]

## Objective

In this part, you will audit the imported database for data integrity issues and propose safe repair actions.

This part tests your ability to think like a database engineer responsible for maintaining correctness and consistency of stored data.

---

## Tasks

### Task 1: Row Count and Import Validation

After importing the dataset, write SQL queries to verify:

- row count of each table
- number of distinct primary key values in each table
- number of NULL or blank values in important columns
- whether any expected table is empty
- whether imported row counts match the raw CSV files

Document your observations.

---

### Task 2: Primary Key and Uniqueness Audit

Write SQL queries to detect:

- duplicate primary key values
- duplicate candidate key values
- duplicate email values, if applicable
- duplicate enrollment records, if applicable
- duplicate contest-problem records, if applicable
- duplicate test-case or test-result records, if applicable
- duplicate attendance records, if applicable

For every check, mention whether the database passed or failed the check.

---

### Task 3: Foreign Key and Relationship Audit

Write SQL queries to detect relationship issues such as:

- students linked to missing batches
- enrollments linked to missing students
- enrollments linked to missing courses
- problems linked to missing courses
- test cases linked to missing problems
- contests linked to missing courses
- contest-problem mappings linked to missing contests or problems
- submissions linked to missing students, problems, or contests
- test results linked to missing submissions or test cases
- sessions linked to missing courses
- attendance linked to missing sessions or students
- regrade requests linked to missing submissions or students
- plagiarism flags linked to missing submissions

For every issue found, explain why it matters.

---

### Task 4: Domain and Rule Validation

Write SQL queries to detect invalid values such as:

- negative scores
- scores greater than maximum allowed marks
- invalid difficulty values
- invalid submission statuses
- invalid programming language values
- invalid test-result statuses
- invalid attendance statuses
- invalid contest statuses
- invalid operation request states
- end time before start time
- resolved time before requested time
- executed time before requested time
- submission timestamp before enrollment date
- NULL or blank values in columns that should be mandatory

You may define additional rule checks based on the dataset.

---

### Task 5: Repair Plan

Create a repair plan for the issues found.

For each issue category, explain whether you would:

- correct the value
- delete the record
- move the record to a rejected/staging table
- ask for manual verification
- leave it unchanged with justification

Your repair plan must include at least 8 specific examples from the dataset using actual IDs.

---

### Task 6: Repair Scripts on Staging Tables

Create a copy/staging version of affected tables and write SQL scripts to safely repair at least 5 issues.

Your scripts should include:

- SELECT query before repair
- UPDATE/DELETE/INSERT correction query
- SELECT query after repair
- comment explaining the decision

Do not directly modify the original imported tables.

---

## Required Repository Files

Your Part 3 GitHub repository must contain:

```text
README.md
import_validation.sql
integrity_audit.sql
domain_rule_checks.sql
repair_plan.md
staging_repair_scripts.sql
before_after_evidence.md
```

---

## Marks Breakdown

| Component | Marks |
|---|---:|
| Import validation and row-count checks | 3 |
| Primary key and uniqueness audit | 4 |
| Foreign key and relationship audit | 5 |
| Domain and rule validation | 4 |
| Repair plan with dataset-specific examples | 5 |
| Safe staging repair scripts with before/after evidence | 4 |
| **Total** | **25** |

---

## Submission Instruction

Submit only the public GitHub repository link for Part 3.

Do not submit any extra text.


---

# Q5. Part 4 — Transactions, Safe Changes & DB Reliability [25 Marks]

## Objective

In this part, you will perform safe data-modification operations and reason about transaction reliability.

This part tests your understanding of DML, transaction control, rollback, commit, savepoints, and ACID properties.

---

## Important Safety Instruction

Do not directly modify your original imported database.

Before performing UPDATE, DELETE, or transaction tasks:

- create a copy of the database, or
- use temporary/staging tables, or
- wrap operations in transactions and rollback while testing

Your repository should clearly show that you handled data modification safely.

---

## Tasks

### Task 1: Safe UPDATE Operations

Write SQL scripts for at least 4 safe UPDATE operations.

Examples may include:

- correcting invalid email values
- correcting missing batch values
- fixing incorrect score values
- updating submission status based on test-result evidence
- updating operation request workflow status after validation

Each UPDATE must include:

- SELECT query used before the update
- UPDATE query
- SELECT query used after the update
- explanation of why the WHERE clause is safe

---

### Task 2: Safe DELETE Operations

Write SQL scripts for at least 2 safe DELETE operations.

Examples may include:

- deleting duplicate staging records
- deleting invalid imported rows from a temporary table
- deleting orphan records after proper verification
- deleting test/dummy records if present

Each DELETE must include:

- SELECT query to identify rows
- DELETE query with a specific WHERE condition
- explanation of why the DELETE does not remove unintended rows

If you believe a record should not be deleted but corrected instead, explain that decision.

---

### Task 3: Transaction Scenarios

Create at least 3 transaction scenarios.

Each scenario must include:

- BEGIN / START TRANSACTION
- one or more INSERT, UPDATE, or DELETE operations
- COMMIT or ROLLBACK
- explanation of expected final database state

At least one scenario must use `ROLLBACK`.

At least one scenario must use `SAVEPOINT` or an equivalent partial rollback approach, if supported by your DBMS.

Suggested scenarios:

1. A student submits a solution and corresponding test-result rows are inserted.
2. A course enrollment is created and then rolled back due to an invalid condition.
3. A score correction is made and committed after validation.
4. Multiple related updates are made with a SAVEPOINT and partial rollback.
5. A regrade request is resolved and related submission score is updated safely.

---

### Task 4: ACID Explanation

Write a Markdown explanation of how ACID properties apply to one of your transaction scenarios.

Your explanation must cover:

- Atomicity
- Consistency
- Isolation
- Durability

Use your own transaction script as the example.

---

### Task 5: Reliability Incident Note

Write a short incident note for one risky database operation.

Example situation:

A developer accidentally runs an UPDATE or DELETE without a WHERE clause in the CodeJudge database.

Your incident note should include:

- what went wrong
- what data could be affected
- how the issue could be detected
- how rollback, backups, or transactions could help
- what preventive measures should be followed in future

This should be specific to this assignment database.

---

## Required Repository Files

Your Part 4 GitHub repository must contain:

```text
README.md
safe_updates.sql
safe_deletes.sql
transactions.sql
acid_explanation.md
incident_note.md
```

---

## Marks Breakdown

| Component | Marks |
|---|---:|
| Safe UPDATE scripts with validation | 5 |
| Safe DELETE scripts with validation | 3 |
| Transaction scenarios | 7 |
| Correct use of COMMIT, ROLLBACK, and/or SAVEPOINT | 3 |
| ACID explanation using own example | 4 |
| Reliability incident note | 3 |
| **Total** | **25** |

---

## Submission Instruction

Submit only the public GitHub repository link for Part 4.

Do not submit any extra text.

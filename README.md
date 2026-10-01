# AccuKnox AI/ML Trainee assignment

Submission by Faleha Qazi ([LinkedIn](https://linkedin.com/in/falehaqazi), [portfolio](https://falehaqazi.github.io)).

This repository has everything for both problem statements. The written answers are in [ANSWERS.md](ANSWERS.md). The same content is also in [Faleha_Qazi_AccuKnox_AIML_Assignment.pdf](Faleha_Qazi_AccuKnox_AIML_Assignment.pdf) if you would rather read it as a document.

## Where each answer is

**Problem Statement 1**

| # | Question | Answer |
| --- | --- | --- |
| 1 | API data retrieval and storage | [task1_books.py](task1_books.py), explained in [A1](ANSWERS.md#a1-task-1-api-data-retrieval-and-storage) |
| 2 | Data processing and visualization | [task2_scores.py](task2_scores.py) and [mock_scores_api.py](mock_scores_api.py), explained in [A2](ANSWERS.md#a2-task-2-data-processing-and-visualization) |
| 3 | CSV data import to a database | [task3_csv_import.py](task3_csv_import.py), explained in [A3](ANSWERS.md#a3-task-3-csv-data-import-to-a-database) |
| 4 | Most complex Python code I have written | [github.com/Falehaqazi/AutoAuth-Clinical-Agent](https://github.com/Falehaqazi/AutoAuth-Clinical-Agent), described in [A4](ANSWERS.md#a4-my-most-complex-python-code) |
| 5 | Most complex database code I have written | [CodeGraphContext PR #1630](https://github.com/CodeGraphContext/CodeGraphContext/pull/1630) (merged), described in [A5](ANSWERS.md#a5-my-most-complex-database-code) |

**Problem Statement, assignment 2**

| # | Question | Answer |
| --- | --- | --- |
| 1 | Self-rating on LLM, Deep Learning, AI, ML | LLM: A, Deep Learning: B, AI: A, ML: A. Reasons in [B1](ANSWERS.md#b1-self-rating) |
| 2 | Architecture of an LLM-based chatbot | [B2](ANSWERS.md#b2-architecture-of-an-llm-based-chatbot) |
| 3 | Vector databases, and choosing one | [B3](ANSWERS.md#b3-vector-databases-and-choosing-one), with the benchmark in [experiments/ann_tradeoff.py](experiments/ann_tradeoff.py) |

## Running the code

Python 3.9 or newer.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python task1_books.py --query "python programming" --limit 20
python task2_scores.py
python task3_csv_import.py data/users.csv

pytest -q                          # 31 tests, no internet needed
```

The databases and the chart are written to `output/`.

No public API serves student test scores, so `mock_scores_api.py` serves `data/students.json` over HTTP, and `task2_scores.py` starts it on a free port when run without `--url`. To run the API separately:

```bash
python mock_scores_api.py --port 8000
python task2_scores.py --url http://127.0.0.1:8000/api/students
```

The benchmark for the vector database answer needs FAISS and takes about 30 seconds:

```bash
pip install faiss-cpu
python experiments/ann_tradeoff.py
```

## Layout

```
common.py              shared HTTP session (timeouts, retries) and SQLite connection
task1_books.py         Task 1
task2_scores.py        Task 2 client: fetch, average, plot
mock_scores_api.py     Task 2 API: GET /api/students
task3_csv_import.py    Task 3
data/                  students.json (served by the API) and users.csv (sample import)
tests/                 pytest suite and an Open Library-style test fixture
experiments/           benchmark and figures for the vector database answer
docs/figures/          figures used in ANSWERS.md
ANSWERS.md             written answers to every question
```

# Tasks: rating set
- [x] 1. `rating_pick` table (model + migration 0016).
- [x] 2. `callsight/analysis/ratingset.py`: pool, even share, agents take turns, create once, queue transcripts, status.
- [x] 3. Rating queue order (blind, rating set, newest); blind pool = rating set when it exists.
- [x] 4. CLI `rating-set create|queue|status`; server task buttons `rating-fetch`, `rating-create`, `rating-status`.
- [x] 5. Tests `tests/test_rating_set.py`.
- [x] 6. 3-agent review, fixes (only picked recordings downloaded, blind waits for the whole set, logs).
- [x] 7. On the user's "ok push": push to main (deploy), rating-status, rating-fetch, rating-create, NLP on, rating-status. → live 2026-11-16; 500 picked, NLP on (main + GPU).

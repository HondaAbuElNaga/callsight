# Spec: Redaction — hide spoken ID, card and phone numbers

**Status:** draft (2026-11-17) · **Plan step:** 7 of [finish plan](../../product/finish-plan.md)
**Roadmap items:** Phase 2 "Later idea: redaction of spoken ID/card/phone numbers (PDPL)" (transcript-view
decision 9).

## Goal
Customers say their national ID, phone or card numbers on calls. These numbers should not be readable by
everyone who opens a transcript, or leave the system in the .txt export or in the text sent to the other system's
ticket (forward summary).

## Scope
**In:** masking in the transcript view, .txt export, AI summary, ticket texts sent to api4.sstli.com; admin can
show the original for one call (logged).
**Out:** muting numbers in the audio; changing the stored original text.

## Requirements
1. **Find:** runs of digits (Arabic-Indic ٠-٩ and 0-9) and spoken Arabic number words (واحد، اثنين... عشرة،
   عشرين... مية، ألف) forming a sequence of ≥ 6 digits, also split across neighbouring segments.
2. **Mask:** replaced by `[رقم مخفي]`; the last 2 digits kept for phone-like numbers (05xx) if the open question
   says so.
3. **Where:** transcript API for every role, .txt export, LLM summary text, the forward ticket notes / followups.
   The LLM itself still gets the original (it runs inside our servers).
4. **Show original:** admin only, per call, with a reason; logged in the audit log.
5. Original text stays in the DB unchanged (retention deletes it as today).

## Acceptance criteria
- [ ] pytest: 30+ sample sentences (digits, words, mixed, split over segments, normal short numbers like "2 days"
      not masked)
- [ ] Export, summary and ticket text contain no 6+ digit sequence (test scans the output)
- [ ] Show-original works for admin only and writes an audit row
- [ ] On 50 real transcripts: the user checks masks (false alarms and misses counted, written in the spec)

## Open questions
1. Keep the last 2 digits of a phone number for the agent to recognise the customer? **Recommended: no** (the
   customer's number is already on the call row).

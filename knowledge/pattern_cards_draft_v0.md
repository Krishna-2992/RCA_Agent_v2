# REMAN RCA – Resolution Pattern Cards (DRAFT v0, for SME review)

**Status:** Draft. Not yet reviewed by a support SME.
**Prepared:** 2026-10-08

## What these are

Each card describes one recurring way the REMAN COBOL application fails: how users report it, what signals identify it, the root cause, the fix that worked before, and how to stop it recurring. The RCA agent will match new incidents against these cards first, then cite the underlying incidents and documentation as evidence.

## Sources used (and not used)

- **Used:** `Reman_samples.xlsx` (69 incidents, 24 service requests, 3 change requests, Apr–Jul 2026) and the AWS Transform documentation (technical XML, program summaries).
- **Deliberately NOT used:** the 45 incidents that appear only in `Reman_incidents_harness.xlsx`. They are the test set. If a reviewer adds knowledge that came from one of those incidents, please mark it **[from harness]** so we can report test results with and without it.

## How to review

For each card, please:
1. Confirm or correct the **root cause**.
2. Fill every **[SME]** gap. These are steps the tickets say were done ("closed the locks", "updated abacus") but never say *how*.
3. Mark each resolution step **OK / Wrong / Unsafe for L1 support**.
4. Add any missing symptom wording users actually use.
5. Say whether the card should be split or merged with another.

## Step source tags

| Tag | Meaning |
|---|---|
| [ticket] | Taken from a resolved incident's resolution notes |
| [doc] | Taken from AWS Transform documentation (generated from the COBOL source) |
| [SME] | Not documented anywhere we have; needs the support team to supply |

Draft confidence is how sure I am that the card is right as written: High / Medium / Low.

---

## Index

| ID | Pattern | System | Incidents in samples | Draft confidence |
|---|---|---|---|---|
| PC-01 | Indexed master file corrupted or locked (mostly IN0011.MST) | Online LMS / Inventory | 17 | High |
| PC-02 | Ticket movement file corrupted (IN0018.MOV / IN0015.MOV): tickets not printing or not transferring | Online LMS / Inventory | 8 | High |
| PC-03 | Control or activity file locked, not corrupted (IN0014.CTL, INKT0300.ACT) | Online | 4 + 2 SRs | Medium |
| PC-04 | Batch abend U4005: abacus (control count) mismatch | Mainframe batch | 4 | Medium |
| PC-05 | Batch abend S0C7 / U4004: non-numeric or bad input data | Mainframe batch | 6 (1 event) | Medium |
| PC-06 | F8RH3910 JOB FAILED RSN=1/2: daily input file missing or malformed | Mainframe batch | 16 (≈5 events) | High |
| PC-07 | Batch abend U2222 at NDM step: SI mailbox slowness | Mainframe batch | 2 (1 event) | Medium |
| PC-08 | Reserve / ticket inconsistency in LMS | Online LMS | 5 + 2 SRs | Medium |
| PC-09 | Sign-on and session problems ("already signed on", cannot log in) | Online | 2 + 3 SRs | Medium |
| PC-10 | Forced password reset at every login (F8RH0251 defect) | Online | 2 SRs + 1 CR | Medium |
| PC-11 | ASN / EDI / file hand-off logic gaps | Batch + Tidal | 1 + 4 SRs | Low |
| PC-12 | STKORD batch running far longer than normal | Batch | 1 | Low |
| PC-13 | QNET file pack and upload (service request) | Process | 4 SRs | Low |
| PC-14 | Printer missing or changed (tickets not printing to a new printer) | Online / print | 2 SRs | Low |
| PC-15 | No fault found / transient / not a COBOL issue | Any | 3 | Medium |

---

## PC-01 · Indexed master file corrupted or locked (mostly IN0011.MST)

**System:** Online, LMS and Inventory (RM/COBOL on desktops, AVD and Zebra devices)
**Seen:** 17 incidents, 2026-06-29 → 2026-07-24. Problem record **PRB0077802** was opened from INC10172270 ("IN0011.mst file has been locking and getting corrupt frequently").
**Draft confidence:** High

**How users report it**
- "LMS won't open" / "can not load lms" / "LMS application will not open at all"
- "LMS error codes" / "LMS is showing an error message"
- "We have a file lock. We are not able to make transactions"
- "Error #9802 occurring" / "file lock fatal error code #9802" / "Open error location file -9802"
- "error # 984 IN0011.MST" / "File lock error 984 IN0011MST Section 5700 writes"
- "Error # 4600 IN0011 No file position" / "not able to print kits … replenishment tickets cannot be completed"
- "Corrupt file causing LMS applications to not function. This is affecting all operators."
- "HOT PARTS MASTER invalid file structure" / "File Lock (Error# 983)"
- Service requests: "Invalid file structure", "A read or start was attempted on an unopened file", "LMS isn't opening for several users"

**Signals**
- Error numbers: 984, 983, 9802, 4600, "invalid file structure"
- File: **IN0011.MST**, the LOCATION-FILE. Also written as IN001.MST, IN0011MST, IN0011.mst. Note: IN001.MST is a typo for IN0011.MST, **not** IN0001.MST (Inventory Master).
- Variant: Hot Parts master (IN0056.MST) or an unnamed master file, same procedure.
- Often affects all users at once. Several tickets for the same event arrive within minutes.

**Root cause**
An indexed data file, most often IN0011.MST, becomes corrupted and/or is left with open locks, so every program that opens it fails. IN0011.MST is opened by both LMS (F8RH0093) and Inventory (F8RH0030), and the program summary rates it "high contention when LMS active" [doc]. Why it corrupts so often is not established. That is the subject of PRB0077802.

**Resolution steps**
1. Identify the affected file from the error text or screenshot. Normalise typos: IN001.MST and IN0011MST mean IN0011.MST. [ticket]
2. Get users out of the file and close all open instances and locks on it ("Closed open instances of file", "closed all the files locks"). [ticket] **How the locks are closed: [SME]**
3. Recover the file. Reman Index (F8RH0071) **Option 34 – File Recovery** runs section 6200-FILE-RECOVERY, which calls **RECOVER1.EXE**. It requires Reman Index security level **4 or above**; below that the option shows as unauthorised. [doc: 01RMNIDX technical documentation]
   - Alternative documented path: Inventory (F8RH0030) option 4 → **F10 File Recovery** (section 1407-RECOVER), which closes and reopens all files. [doc: 02RMNINVTR technical documentation] **Which path support uses and when: [SME]**
4. Ask the user to reopen LMS / Inventory and confirm transactions work. [ticket]

**Verify:** the user confirms LMS opens and transactions post; the error does not return.

**Preventive actions**
- Link every occurrence to PRB0077802; this file failed about 17 times in 4 weeks, so the fix is permanent root-cause work, not repeated recovery.
- [SME] Is there monitoring (for example on file errors) that could detect corruption before users do?

**Escalate when:** recovery fails, the same file corrupts again within hours, or the error names a file you can't identify.

**Don't confuse with**
- PC-02 (ticket files IN0018.MOV / IN0015.MOV): symptom is tickets not printing or not transferring.
- PC-03 (control-file lock, error 9302): unlock only, usually no recovery.

**Evidence:** INC10243056, INC10239216, INC10239214, INC10207132, INC10206730, INC10206709, INC10206697, INC10205898, INC10205871, INC10173980, INC10172270, INC10153437, INC10152406, INC10138881, INC10161392 (file not named), INC10173457 (Hot Parts master), INC10173454 (error 983). SRs SCTASK4908233, SCTASK4889477, SCTASK4891637 (symptom only).

**Questions for reviewer**
- INC10243056's notes say "Inventory Master was corrupted" while the error names IN0011.MST (the Location file). Which is right?
- Does error 4600 ("No file position") always mean corruption, or sometimes just a lock?

---

## PC-02 · Ticket movement file corrupted (IN0018.MOV / IN0015.MOV)

**System:** Online, LMS / Inventory ticket printing and transfer
**Seen:** 8 incidents, 2026-04-29 → 2026-07-20
**Draft confidence:** High

**How users report it**
- "Trying to print tickets … a 46 pc kit is only printing 3 tickets"
- "4 pm line checker tickets did not fully print … 1100-READ Error IN0018"
- "The 1 pm line checker tickets have not printed out"
- "error 984 IN0015.MOR … tickets print but do not transfer into LMS and INV"
- "File lock file name IN0018.MOL" / "File Lock Error" / "receiving error in lms"
- "LMS randomly didn't print about 200 tickets from the 9am, 11am, 1pm and 4pm line checker drops"

**Signals**
- Files: **IN0018.MOV** (issue tickets: control numbers, parts, quantities, cells, line checker [doc]) and **IN0015.MOV** (control-ticket records processed on receipt [doc]).
- Typos seen: IN0018.MOL, IN0018.MST, IN0015.MOR.
- Symptom is about **tickets**: missing, partial, not transferring. LMS itself usually still opens.

**Root cause**
The ticket movement file is corrupted or locked, so ticket records are not written or read completely. For the "~200 tickets not printed" case the likely cause is that entries were never written to IN0018.MOV while it was locked [ticket, INC9907453, still under analysis there].

**Resolution steps**
1. Identify which file the error names (IN0018.MOV for issue / line-checker tickets, IN0015.MOV for receipt tickets). [ticket]
2. Close all locks on the file. [ticket] **How: [SME]**
3. Recover the file: Reman Index **Option 34** (RECOVER1.EXE, security level ≥ 4). [doc] **Confirm this is the same path used for .MOV files: [SME]**
4. Reprint or re-drive the tickets that were missed, and confirm with the user. **How to find and reprint the missed tickets (Report 73?): [SME]**

**Verify:** the next ticket run prints the full count; missed tickets are accounted for.

**Preventive actions**
- Repeated corruption of the same file: add it to the PRB0077802 investigation.
- [SME] A check that compares tickets expected against tickets printed per line-checker drop, which is what the reporter of INC9907453 asked for.

**Don't confuse with**
- SCTASK4586368: tickets ordered 23:00–23:59 failing to print on the 5 am run every day. That is a timing / program issue (F8RH0087 under analysis), not file corruption.
- PC-14: printer changed or missing.

**Evidence:** INC10221086, INC10163159, INC10154256, INC10153838, INC10114768, INC10114752, INC10071252, INC9907453

---

## PC-03 · Control or activity file locked, not corrupted

**System:** Online (light decon, kit printing)
**Seen:** 4 incidents + 2 SRs, 2026-06-08 → 2026-07-01
**Draft confidence:** Medium

**How users report it**
- "we have a file lock in light decon error code 9302"
- "file lock error code 9302 file name IN0014.CTL"
- "COBOL application shows File Lock when printing a kit and paperwork does not print out correctly"
- "file locked, can't receive" (Boonville)

**Signals**
- Error **9302**
- **IN0014.CTL** (light decon control file; not described in any documentation, known only from tickets) and **INKT0300.ACT** (kit printing)

**Root cause:** a lock was left on a control or activity file. Tickets show no corruption; releasing the lock was enough.

**Resolution steps**
1. Identify the locked file from the error. [ticket]
2. Release the lock on that file. [ticket] **How: [SME]**
3. Have the user retry. If the error comes back immediately, treat it as PC-01 (recover the file). [SME confirm]

**Preventive actions:** [SME] Why does IN0014.CTL lock repeatedly? 3 occurrences in 4 weeks.

**Evidence:** INC10148317, INC10109021, INC10055662 (IN0014.CTL); INC10070575 and SR SCTASK4841814 (INKT0300.ACT); SR SCTASK4868281 (no notes)

---

## PC-04 · Batch abend U4005: abacus mismatch

**System:** Mainframe batch via AJS scheduler
**Seen:** 4 incidents, 2026-07-08 → 2026-07-27
**Draft confidence:** Medium

**How it arrives:** automated alert, for example `Incident AJS002E ABEND=S000 U4005 F8RH0430(JOB…) JCL=A.F8RH0410 Z1SCH…`, or `F8RH0920 … JCL=A.F8RH0910`

**Signals**
- Abend **U4005**
- Step F8RH0430 in JCL A.F8RH0410, or F8RH0920 in A.F8RH0910
- Dataset **VSAMPROD.F8RH.F810D006**

**Root cause:** the abacus (control count) for the dataset does not match what the job finds. **What abacus is and what feeds it: [SME]**

**Resolution steps**
1. Confirm from the job log that the U4005 is an abacus mismatch. Caution: on 2026-07-24 a U4005 on F8RH0430 was actually bad data (see PC-05).
2. Update the abacus for the dataset. [ticket] **How: [SME]**
3. Resubmit the job from the failed step and confirm it completes. [ticket]
   - Variant: "Bypass abacus and restarted the job" (INC10248653). **When is bypassing acceptable? [SME]**

**Preventive actions:** 4 occurrences in 3 weeks. Find out why the count drifts upstream rather than updating it each time. [SME]

**Escalate when:** the mismatch repeats on consecutive runs.

**Evidence:** INC10248653, INC10179067, INC10174315, INC10174054

---

## PC-05 · Batch abend S0C7 / U4004: non-numeric or bad input data

**System:** Mainframe batch (F8RH0410 stream: steps F8RH0415, F8RH0430, F8RH0435)
**Seen:** 6 incidents, 2026-07-14 → 2026-07-24. Five of them are one event on 2026-07-24.
**Draft confidence:** Medium

**How it arrives:** `AJS002E ABEND=S0C7 U0000 F8RH0415 …`, `ABEND=S000 U4004 F8RH0430/F8RH0435 …` (and one U4005)

**Root cause:** a record with non-numeric data in a numeric field (S0C7 data exception), or an incorrect input file.

**Resolution steps**
1. Identify the failing record from the abend output. **How: [SME]**
2. Remove or correct the bad data. [ticket]
3. Restart the job and confirm it completes. [ticket]

**Preventive actions:** [SME] Where does the bad data originate? Can the upstream extract validate numeric fields?

**Related:** INC10193118, an ad hoc F8RH8010 run updating master and VSAM files, fixed by correcting the input file and rerunning.

**Evidence:** INC10243474, INC10243436, INC10243434, INC10243369, INC10243217, INC10193118

---

## PC-06 · F8RH3910 JOB FAILED RSN=1/2: daily input file missing or malformed

**System:** Mainframe batch, with inputs arriving via Tidal / NDM from the LAN side
**Seen:** 16 incidents, about 5 distinct events, 2026-06-05 → 2026-07-16. INC10139580 alone has 10 child incidents.
**Draft confidence:** High

**How it arrives:** `Incident AJS002I JOB FAILED RSN=1 (or RSN=2) F8RH3910(JOB…) JCL=A.F8RH3910 …`, sometimes `ABEND=S000 U4000` or `U3336`

**Sub-causes seen**

| Sub-cause | Evidence | Fix [ticket] |
|---|---|---|
| Header / trailer records missing in the input file | INC10139580 + 10 children | Add the header, remove the extra trailer, rerun |
| Daily upload file from Tidal not received | INC10206728 | Work with the Tidal team; restart once the file arrives |
| Tidal could not NDM the file | INC10051293, INC10051485 | Tidal team reruns the NDM; restart the job |
| LAN files not transferred to host (JCL error), then blank data in WRK.F8RH.P1390191 | INC10077860, INC10076965 | Resubmit once the file is available; remove the bad data |

**Resolution steps**
1. Check whether the expected input datasets exist and are complete. [ticket] **Which datasets F8RH3910 needs: [SME]**
2. If missing: contact the Tidal team, confirm the NDM transfer, and restart once the file arrives. [ticket]
3. If malformed: correct the header/trailer or remove bad records, then rerun. [ticket]
4. Repeated alerts for the same job and day are one event. Resolve the parent and link the children (as was done for INC10139580). [ticket]

**Preventive actions:** [SME] A pre-check that the input file exists and has a valid header/trailer before F8RH3910 is triggered.

**Evidence:** INC10139580 (parent of 10), INC10206728, INC10051293, INC10051485, INC10077860, INC10076965

---

## PC-07 · Batch abend U2222 at NDM step: SI mailbox slowness

**System:** Mainframe batch F8RH5010
**Seen:** 2 incidents, 1 event, 2026-06-15
**Draft confidence:** Medium

**Signals:** `ABEND=S000 U2222 F8RH5010 … JCL=A.F8RH5010`; failure at the NDM step pulling from the SI mailbox

**Root cause:** SI (Sterling Integrator) production slowness; the job hit its maximum delay.

**Resolution steps**
1. Confirm the failure is at the NDM/SI extraction step. [ticket]
2. Ask the SI team to check or refresh the production server. [ticket]
3. Restart the job. [ticket]

**Evidence:** INC10083167, INC10082522

---

## PC-08 · Reserve / ticket inconsistency in LMS

**System:** Online LMS / Inventory
**Seen:** 5 incidents + 2 SRs, 2026-04-22 → 2026-07-26
**Draft confidence:** Medium

**How users report it**
- "control number … is still an open reserve ticket, however the inventory is no longer available"
- "Ticket is a live ticket but not on reserve … Location does not exist for part number"
- "We need a part number reset so we can delete a glitch ticket"
- "reserved ticket quantity of 800 pieces but the issue ticket has a quantity of 1,050"
- "Reserved parts for non-pickable location … force ticket … location D04444C1"

**Root causes seen**
- The reserve was not updated, and another user's transfer emptied (and so deleted) the location. [ticket]
- Transactions made during the batch update window caused mismatched reserve and quantity. [SR SCTASK4900529]
- The FIFO flag ('N' for D-type locations, kept in REMAN.NDX) was not fetched, so a non-pickable location was reserved. [ticket, INC9876850]

**Resolution steps**
1. Inquire on the part and the ticket in LMS to confirm the mismatch. [ticket]
2. Kill the stale ticket and create a new one. [ticket]
3. For a "glitch ticket": inquire the part number → **F4** to reset. [ticket, INC10174275]
4. If it recurs for the same location type or part, escalate for program analysis (code change). [ticket]

**Preventive actions**
- [SME] Should transactions be blocked or warned against during the batch update window?
- [SME] Why is the FIFO flag sometimes not read from REMAN.NDX?

**Escalation:** often needs a developer when it recurs.

**Evidence:** INC10245587, INC10177489, INC10174275, INC10152102, INC9876850; SRs SCTASK4900529, SCTASK4770623

---

## PC-09 · Sign-on and session problems

**System:** Online (Reman Index security F8RH0251 used by LMS / Inventory)
**Seen:** 2 incidents + 3 SRs, 2026-05-08 → 2026-07-07
**Draft confidence:** Medium

**How users report it**
- "LMS is not allowing ee to log in" / "already logged on"
- "CANNOT LOG INTO LMS THROUGH AVD"
- "keeps getting kicked out of his LMS and has to be reset"
- "LMS won't open on computers or Zebras. It just pops up the black screen and closes"

**Signals:** the security call (F8RH0251) returns code **3 "YOU ARE ALREADY SIGNED ON"** or code **2 "Your SignOn Can Not Be Located"** [doc: 05RHE247 technical documentation]

**Root cause:** a stale session or temp login files left from a previous session (user signed on elsewhere, or a session not closed).

**Resolution steps**
1. Ask for the exact message. "Already signed on" and "SignOn not located" need different handling. [doc]
2. "Already signed on": the user signs off the other session. If it is stale, delete the user's temp login files. [ticket, INC10071103] Reman Index creates and removes a per-user temp directory `\TEMP\{UID}` [doc: 01RMNIDX]. **Is that the folder to clear? [SME]**
3. "SignOn not located": check that the user ID exists in Reman Index with the right access level. [SME]
4. If it affects many users at once, check PC-01 (a corrupt file can present as "won't open").

**Evidence:** INC10071103, INC9942848; SRs SCTASK4826741, SCTASK4752211, SCTASK4912046

---

## PC-10 · Forced password reset at every login (F8RH0251 defect)

**System:** Online, Reman Index security
**Seen:** 2 SRs (2026-04-30, 2026-05-22) and change **CHG0801085** "F8RH0251 No Password (PWD) Issue Fix" (2026-05-04 → 2026-05-26)
**Draft confidence:** Medium

**How users report it:** "has to reset his password in LMS every single day" / "every time it makes her reset her password"

**Root cause:** a bug in F8RH0251 [SR work note], fixed by CHG0801085.

**Resolution steps**
1. If reported **after 2026-05-26**, treat it as a possible regression of CHG0801085 and escalate to development. [ticket/CR]
2. A single user who has simply forgotten a password is a normal password reset; follow KB0010531 (SR SCTASK4891606). **Steps: [SME]**

**Evidence:** SCTASK4728202, SCTASK4789474, CHG0801085, SCTASK4891606

---

## PC-11 · ASN / EDI / file hand-off logic gaps

**System:** Batch ASN process (F8RH7910, quickjob F8RHC071) and Tidal EDI
**Seen:** 1 incident + 4 SRs
**Draft confidence:** Low

**Examples**
- A new facility/dock code (TO_FAC=R6, DOCK_CODE=DTR615) needed a header change. Root cause: F8RH7910 lacked the logic to convert R6 → 47 in quickjob F8RHC071, so the logic was added (code change). [ticket, INC10183770]
- ASN not created for a load. [SR SCTASK4881141]
- SS0001.rel not produced for the Tidal EDI job. [SRs SCTASK4821596, SCTASK4815228]

**Resolution:** usually a developer investigation or code change. Escalation: code_change.

**Evidence:** INC10183770; SRs SCTASK4897775, SCTASK4881141, SCTASK4821596, SCTASK4815228

---

## PC-12 · STKORD batch running far longer than normal

**Seen:** 1 incident (2026-05-04). Draft confidence: Low.

**Report:** "STKORD job started at 6:00 AM … still running at 9:40. It normally runs in around 15 minutes."

**Resolution:** the job was cancelled and restarted from program F8RH0199. [ticket] **Root cause of the slowness: unknown. [SME]**

**Evidence:** INC9920127

---

## PC-13 · QNET file pack and upload (service request)

**Seen:** 4 SRs. Draft confidence: Low.

**Requests:** "QNET File upload" / "please pack and upload this file" / "Negative qty's are not coming through negative" (SCTASK4737983, which may be a defect).

**Steps: [SME]**, not documented in tickets or docs.

**Evidence:** SCTASK4896276, SCTASK4870167, SCTASK4734673, SCTASK4737983

---

## PC-14 · Printer missing or changed

**Seen:** 2 SRs. Draft confidence: Low.

**Examples:** printer F8HPPRNB130 missing from the LMS printer list; printer changed at the Sawyer Road location so line checkers stopped printing.

**Steps: [SME].** The program catalog lists F8RH0304 / F8RHI304 (network printer configuration for workstations) and F8RH0436 (default receipt printers), but that catalog is AI-generated and low-trust.

**Evidence:** SCTASK4888563, SCTASK4887992

---

## PC-15 · No fault found / transient / not a COBOL issue

**Seen:** 3 incidents. Draft confidence: Medium.

**Examples**
- "LMS has completely frozen on all systems" → resolved itself after the user minimised the window
- A screenshot error that was not a COBOL issue
- "equipment will not load" → could not be reproduced

**Guidance**
1. Ask for the exact error text or a screenshot, and whether other users are affected.
2. If it cleared by itself, close as not reproducible.
3. If it recurs or names a file or error number, re-route to PC-01, PC-02 or PC-03.

**Evidence:** INC10183373, INC10181601, INC9998727

---

## Gaps the cards cannot fill yet (please supply if possible)

1. How support closes file locks (the exact tool or command).
2. What "abacus" is for VSAMPROD.F8RH.F810D006, and how it is updated.
3. Which input datasets F8RH3910 needs, and where they come from.
4. The meaning of the RM/COBOL error numbers 984, 983, 9802, 9302 and 4600 (no document explains them).
5. QNET pack/upload steps, and printer setup steps.
6. Findings from PRB0077802 (why IN0011.MST corrupts).

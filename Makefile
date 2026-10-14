CC ?= cc
CFLAGS ?= -std=c99 -O2 -Wall -Wextra -Wpedantic -D_POSIX_C_SOURCE=200809L -D_DEFAULT_SOURCE
INC = -Icore/include -Ievaluation/include
BUILD = build
OBJS = $(BUILD)/audio.o $(BUILD)/features.o $(BUILD)/dtw.o $(BUILD)/resample.o $(BUILD)/qa.o
EVOBJS = $(BUILD)/sha256.o $(BUILD)/csv.o $(BUILD)/dataset.o $(BUILD)/json.o $(BUILD)/pitch.o $(BUILD)/settings.o $(BUILD)/eval.o $(BUILD)/degrade.o
TESTS = $(BUILD)/test_audio $(BUILD)/test_features $(BUILD)/test_dtw $(BUILD)/test_recording $(BUILD)/test_dataset $(BUILD)/test_eval $(BUILD)/test_degrade

all: $(BUILD)/s2w_degrade $(BUILD)/s2w_qa $(BUILD)/s2w_ingest $(BUILD)/s2w_eval $(BUILD)/s2w_dataset_check $(BUILD)/s2w_make_fixture $(BUILD)/s2w_inspect $(BUILD)/s2w_features $(BUILD)/s2w_match $(TESTS)

$(BUILD)/%.o: core/src/%.c core/include/*.h | $(BUILD)
	$(CC) $(CFLAGS) $(INC) -c $< -o $@

$(BUILD)/%.o: evaluation/src/%.c core/include/*.h evaluation/include/*.h | $(BUILD)
	$(CC) $(CFLAGS) $(INC) -c $< -o $@

$(BUILD)/s2w_dataset_check: tools/s2w_dataset_check.c $(OBJS) $(EVOBJS)
	$(CC) $(CFLAGS) $(INC) $^ -o $@ -lm

$(BUILD)/s2w_degrade: tools/s2w_degrade.c $(OBJS) $(EVOBJS)
	$(CC) $(CFLAGS) $(INC) $^ -o $@ -lm

$(BUILD)/s2w_qa: tools/s2w_qa.c $(OBJS)
	$(CC) $(CFLAGS) $(INC) $^ -o $@ -lm

$(BUILD)/s2w_ingest: tools/s2w_ingest.c $(OBJS) $(EVOBJS)
	$(CC) $(CFLAGS) $(INC) $^ -o $@ -lm

$(BUILD)/s2w_eval: tools/s2w_eval.c $(OBJS) $(EVOBJS)
	$(CC) $(CFLAGS) $(INC) $^ -o $@ -lm

$(BUILD)/s2w_make_fixture: tools/s2w_make_fixture.c $(OBJS) $(EVOBJS)
	$(CC) $(CFLAGS) $(INC) $^ -o $@ -lm

$(BUILD)/s2w_inspect: tools/s2w_inspect.c $(OBJS)
	$(CC) $(CFLAGS) $(INC) $^ -o $@ -lm

$(BUILD)/s2w_features: tools/s2w_features.c $(OBJS)
	$(CC) $(CFLAGS) $(INC) $^ -o $@ -lm

$(BUILD)/s2w_match: tools/s2w_match.c $(OBJS)
	$(CC) $(CFLAGS) $(INC) $^ -o $@ -lm

$(BUILD)/test_audio $(BUILD)/test_features $(BUILD)/test_dtw $(BUILD)/test_recording: $(BUILD)/test_%: core/tests/test_%.c $(OBJS)
	$(CC) $(CFLAGS) $(INC) $^ -o $@ -lm

$(BUILD)/test_dataset: evaluation/tests/test_dataset.c $(OBJS) $(EVOBJS) $(BUILD)/s2w_make_fixture
	$(CC) $(CFLAGS) $(INC) evaluation/tests/test_dataset.c $(OBJS) $(EVOBJS) -o $@ -lm

$(BUILD)/test_degrade: evaluation/tests/test_degrade.c $(OBJS) $(EVOBJS)
	$(CC) $(CFLAGS) $(INC) evaluation/tests/test_degrade.c $(OBJS) $(EVOBJS) -o $@ -lm

$(BUILD)/test_eval: evaluation/tests/test_eval.c $(OBJS) $(EVOBJS) $(BUILD)/s2w_make_fixture $(BUILD)/s2w_eval
	$(CC) $(CFLAGS) $(INC) evaluation/tests/test_eval.c $(OBJS) $(EVOBJS) -o $@ -lm

$(BUILD):
	mkdir -p $@

test: $(TESTS)
	@for t in $(TESTS); do $$t $(BUILD) || exit 1; done
	@$(MAKE) --no-print-directory test-review BUILD=$(BUILD)

# Word-list review tooling (skipped automatically without python3 + openpyxl)
test-review: $(BUILD)/s2w_make_fixture $(BUILD)/s2w_dataset_check $(BUILD)/s2w_ingest $(BUILD)/s2w_qa $(BUILD)/s2w_eval $(BUILD)/s2w_degrade $(BUILD)/s2w_features $(BUILD)/s2w_match
	@S2W_BUILD=$(BUILD) python3 -I tools/test_review_sheet.py
	@S2W_BUILD=$(BUILD) python3 -I tools/test_ingest.py
	@S2W_BUILD=$(BUILD) python3 -I tools/test_robustness.py
	@S2W_BUILD=$(BUILD) python3 -I tools/test_enrolment_experiment.py
	@S2W_BUILD=$(BUILD) python3 -I tools/test_pilot_plan.py
	@S2W_BUILD=$(BUILD) python3 -I tools/test_consent_gate.py
	@S2W_BUILD=$(BUILD) python3 -I tools/test_viewer.py
	@S2W_BUILD=$(BUILD) python3 -I tools/test_recorder.py
	@S2W_BUILD=$(BUILD) python3 -I tools/test_lexicon.py
	@S2W_BUILD=$(BUILD) python3 -I tools/test_alphabet.py
	@S2W_BUILD=$(BUILD) python3 -I tools/test_wordsearch.py

# Same suite under AddressSanitizer + UndefinedBehaviorSanitizer (leaks fail the run).
test-asan:
	$(MAKE) BUILD=build-asan CFLAGS="-std=c99 -g -O1 -fsanitize=address,undefined -fno-sanitize-recover=all -Wall -Wextra -Wpedantic -D_POSIX_C_SOURCE=200809L -D_DEFAULT_SOURCE" test

# Optional: run over real recordings in $(S2W_REAL_WAV_DIR); skipped when unset.
test-real: all
	@if [ -z "$(S2W_REAL_WAV_DIR)" ] || [ ! -d "$(S2W_REAL_WAV_DIR)" ]; then \
	  echo "test-real: S2W_REAL_WAV_DIR not set; skipping (synthetic tests cannot establish speech quality)"; \
	else n=0; for f in $(S2W_REAL_WAV_DIR)/*.wav; do \
	  $(BUILD)/s2w_inspect "$$f" >/dev/null; [ $$? -le 1 ] || { echo "inspect failed: $$f"; exit 1; }; \
	  $(BUILD)/s2w_features "$$f" >/dev/null || { echo "features failed: $$f"; exit 1; }; n=$$((n+1)); done; \
	  echo "test-real: $$n files ok"; fi

clean:
	rm -rf build build-asan

.PHONY: all test test-review test-asan test-real clean

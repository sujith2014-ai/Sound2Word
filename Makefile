CC ?= cc
CFLAGS ?= -std=c99 -O2 -Wall -Wextra -Wpedantic -D_POSIX_C_SOURCE=200809L -D_DEFAULT_SOURCE
INC = -Icore/include
BUILD = build
OBJS = $(BUILD)/audio.o $(BUILD)/features.o $(BUILD)/dtw.o
TESTS = $(BUILD)/test_audio $(BUILD)/test_features $(BUILD)/test_dtw

all: $(BUILD)/s2w_inspect $(BUILD)/s2w_features $(BUILD)/s2w_match $(TESTS)

$(BUILD)/%.o: core/src/%.c core/include/*.h | $(BUILD)
	$(CC) $(CFLAGS) $(INC) -c $< -o $@

$(BUILD)/s2w_inspect: tools/s2w_inspect.c $(OBJS)
	$(CC) $(CFLAGS) $(INC) $^ -o $@ -lm

$(BUILD)/s2w_features: tools/s2w_features.c $(OBJS)
	$(CC) $(CFLAGS) $(INC) $^ -o $@ -lm

$(BUILD)/s2w_match: tools/s2w_match.c $(OBJS)
	$(CC) $(CFLAGS) $(INC) $^ -o $@ -lm

$(BUILD)/test_%: core/tests/test_%.c $(OBJS)
	$(CC) $(CFLAGS) $(INC) $^ -o $@ -lm

$(BUILD):
	mkdir -p $@

test: $(TESTS)
	@for t in $(TESTS); do $$t || exit 1; done

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

.PHONY: all test test-asan test-real clean

CC ?= cc
CFLAGS ?= -std=c99 -O2 -Wall -Wextra -Wpedantic -D_POSIX_C_SOURCE=200809L -D_DEFAULT_SOURCE
INC = -Icore/include
BUILD = build

all: $(BUILD)/s2w_inspect $(BUILD)/test_audio

$(BUILD)/%.o: core/src/%.c core/include/*.h | $(BUILD)
	$(CC) $(CFLAGS) $(INC) -c $< -o $@

$(BUILD)/s2w_inspect: tools/s2w_inspect.c $(BUILD)/audio.o
	$(CC) $(CFLAGS) $(INC) $^ -o $@ -lm

$(BUILD)/test_audio: core/tests/test_audio.c $(BUILD)/audio.o
	$(CC) $(CFLAGS) $(INC) $^ -o $@ -lm

$(BUILD):
	mkdir -p $@

test: $(BUILD)/test_audio
	$(BUILD)/test_audio

clean:
	rm -rf $(BUILD)

.PHONY: all test clean

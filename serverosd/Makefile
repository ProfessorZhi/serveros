# The native L1 device runtime, built without CMake.
#
# WHY A MAKEFILE AND NOT A PYTHON DRIVER
# --------------------------------------
# `native/node-core/` is a working CMake project and that stays the primary
# build. The problem is narrower: the device runtime has to be buildable on a
# host that has a C++20 compiler and nothing else, and this Dell is one —
# `cmake` and `ctest` are not installed here (see the README's measured table).
#
# A Python driver was the obvious first shape and it was the wrong one. The
# repository's actuator gate (`skills/serveros/scripts/mutation_guard.py`) scans
# every `*.py` in the tree and refuses a command it cannot find declared in
# `runtime/actuator-contract.yaml`. A compiler driver is `subprocess.run` with an
# argv the scanner cannot resolve, so it would have arrived as six
# UNDECLARED_ACTUATOR findings — reachability nobody reviewed, added by a lane
# that does not own the contract it would have had to edit.
#
# A build recipe is the same kind of artifact as the CMakeLists next door: it
# declares *what* to run, and the operator's `make` runs it, exactly as their
# `cmake --build` would. It is not new reachability in the product. That is why
# this is a Makefile — and why the Python beside it (`package_manifest.py`,
# `verify_native_runtime.py`) records and verifies without running anything.
#
# WHERE OUTPUT GOES
# -----------------
# `OUT` is required and must be outside the repository. A build directory in the
# source tree is how a tree becomes dirty at an hour nobody was watching, and
# `make` will refuse rather than create one.
#
# THE ONE-NAME RULE
# -----------------
# The package is called `serverosd`; the binaries keep their names. Renaming them
# would break the Python adapter, the status-socket path and the generation-fence
# contract, and it would claim `bin/serverosd`, which belongs to the Python node
# runtime artifact. `verify_native_runtime.py` enforces that; this file only has
# to not undo it.

REPO := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
# Canonicalised too: a checkout reached through a symlink would otherwise compare
# an unresolved REPO against a resolved OUT and let the tree through.
REPO_REAL := $(realpath $(REPO))

OUT ?=
CXX ?= g++
JOBS ?= 2

ifeq ($(strip $(OUT)),)
$(error OUT is required: make OUT=/some/path/outside/the/repository)
endif

# `override` IS THE GUARD. A variable given on the command line beats a
# makefile assignment, so a plain `OUT := $(abspath $(OUT))` here silently does
# nothing: measured, `OUT=build` passed the check below and then created
# `build/obj` inside the tree, and `OUT=/tmp/../home/fjd/projects/serveros/x`
# reached the compiler with its `..` intact. The check looked right and was
# inert, which is worse than absent.
override OUT := $(abspath $(OUT))

# `abspath` normalises `..` but does not follow symlinks, so resolve the deepest
# part of the path that exists. A path whose parent does not exist yet cannot be
# resolved here; `package_manifest.py` repeats the check with `Path.resolve`
# after the build, so the manifest cannot certify a package written into the
# tree even if the compiler got that far.
OUT_REAL := $(realpath $(OUT))
ifeq ($(strip $(OUT_REAL)),)
override OUT_REAL := $(realpath $(dir $(OUT)))/$(notdir $(OUT))
endif
# TWO patterns, and the bare one is not decoration: `$(filter $(REPO)/%,...)`
# requires a literal slash after REPO, so `OUT=.`, `OUT=$(pwd)` and
# `OUT=<repo>/` all passed it and then wrote `obj/`, `bin/` and PACKAGE.json into
# the tree — linking the test binaries into the existing `tests/` directory.
# Measured, before this line existed.
ifneq ($(filter $(REPO_REAL) $(REPO_REAL)/%,$(OUT_REAL)),)
$(error refusing to build inside the repository: $(OUT_REAL) is the repository or under it)
endif

CXXFLAGS ?= -std=c++20 -O2 -Wall -Wextra -Wpedantic
INCLUDES := \
  -I$(REPO)/native/node-core/include \
  -I$(REPO)/native/node-core/supervisor/include \
  -I$(REPO)/native/node-core/supervisor/src \
  -I$(REPO)/native/node-core/core-lite/include \
  -I$(REPO)/native/node-core/tests

# Provenance for PACKAGE.json. Read here, in the recipe, and handed to the pure
# Python that writes the manifest — rather than by the Python running `git`.
SOURCE_COMMIT := $(shell git -C $(REPO) rev-parse HEAD 2>/dev/null)
# A COUNT, not the first character of porcelain output: the first character of a
# modified file is a space, and a value that is stripped on its way through a
# shell variable would then read as "clean".
#
# TWO invocations, and the first exists only for its exit status. `... | wc -l`
# prints `0` whenever `wc` runs, including when `git status` failed, so a single
# pipeline reports "clean" for a repository git could not read. Measured:
# `git -C /nonexistent/repo status --porcelain | wc -l` -> 0. With the guard,
# a failed status leaves this EMPTY, which `package_manifest.py` records as
# unmeasured rather than as zero modified paths.
SOURCE_DIRTY := $(shell git -C $(REPO) status --porcelain >/dev/null 2>&1 && \
                   git -C $(REPO) status --porcelain 2>/dev/null | wc -l | tr -d ' ')
# The compiler's own identity, asked here rather than by the manifest writer for
# the same reason: a recorder must not grow reachability.
CXX_VERSION := $(shell $(CXX) --version 2>/dev/null | head -n 1)

# The library sources, split the way the CMake targets split them.
NODECORE_SRC := \
  native/node-core/src/json.cpp \
  native/node-core/src/time.cpp \
  native/node-core/src/diff.cpp \
  native/node-core/src/linux_readers.cpp \
  native/node-core/src/sample.cpp \
  native/node-core/src/snapshot.cpp

SUPERVISOR_SRC := \
  native/node-core/supervisor/src/sha256.cpp \
  native/node-core/supervisor/src/model.cpp \
  native/node-core/supervisor/src/manifest.cpp \
  native/node-core/supervisor/src/recovery.cpp \
  native/node-core/supervisor/src/plan.cpp \
  native/node-core/supervisor/src/provider.cpp \
  native/node-core/supervisor/src/proc.cpp \
  native/node-core/supervisor/src/systemd_user.cpp \
  native/node-core/supervisor/src/admission.cpp \
  native/node-core/supervisor/src/action_gate.cpp \
  native/node-core/supervisor/src/action_journal.cpp \
  native/node-core/supervisor/src/facts.cpp \
  native/node-core/supervisor/src/state_directory.cpp \
  native/node-core/supervisor/src/probe.cpp \
  native/node-core/supervisor/src/lease.cpp \
  native/node-core/supervisor/src/receipt.cpp \
  native/node-core/supervisor/src/status.cpp \
  native/node-core/supervisor/src/serve.cpp \
  native/node-core/supervisor/src/status_socket.cpp \
  native/node-core/supervisor/src/capability.cpp

CORELITE_SRC := \
  native/node-core/core-lite/src/profile.cpp \
  native/node-core/core-lite/src/status.cpp \
  native/node-core/core-lite/src/identity.cpp \
  native/node-core/core-lite/src/gpu_readings.cpp \
  native/node-core/core-lite/src/service_readings.cpp \
  native/node-core/core-lite/src/collector_readings.cpp

# `main()` translation units. Compiled like any other source; they are named
# separately because only the link step distinguishes them.
MAIN_NATIVE_CORE := native/node-core/supervisor/src/main.cpp
MAIN_SNAPSHOT := native/node-core/src/main.cpp
MAIN_CORE_OBJECTS := \
  native/node-core/core-lite/src/status_main.cpp \
  native/node-core/core-lite/src/profile_check.cpp

ALL_SRC := $(NODECORE_SRC) $(SUPERVISOR_SRC) $(CORELITE_SRC) \
           $(MAIN_NATIVE_CORE) $(MAIN_SNAPSHOT) $(MAIN_CORE_OBJECTS)

rel = $(patsubst $(REPO)/%,%,$(1))
obj = $(OUT)/obj/$(subst /,__,$(call rel,$(1))).o

LIB_NODECORE := $(foreach s,$(NODECORE_SRC),$(call obj,$(REPO)/$(s)))
LIB_SUPERVISOR := $(foreach s,$(SUPERVISOR_SRC),$(call obj,$(REPO)/$(s)))
LIB_CORELITE := $(foreach s,$(CORELITE_SRC),$(call obj,$(REPO)/$(s)))

# `SUPERVISOR_SOURCE_DIR` is the one definition a CMake test actually reads: it
# lets `test_sup_manifest` parse the committed example rather than a copy.
SUPERVISOR_SOURCE_DIR := -DSUPERVISOR_SOURCE_DIR='"$(REPO)/native/node-core/supervisor"'

BIN_NATIVE_CORE := $(OUT)/bin/serveros-native-core
BIN_SNAPSHOT := $(OUT)/bin/serveros-node-resource-snapshot
BIN_CORE_STATUS := $(OUT)/bin/core-lite-status
BIN_CORE_CHECK := $(OUT)/bin/core-lite-profile-check

BINARIES := $(BIN_NATIVE_CORE) $(BIN_SNAPSHOT) $(BIN_CORE_STATUS) $(BIN_CORE_CHECK)

COLLECTOR_TESTS := $(wildcard $(REPO)/native/node-core/tests/test_*.cpp)
SUPERVISOR_TESTS := $(wildcard $(REPO)/native/node-core/supervisor/tests/test_*.cpp)
CORELITE_TESTS := $(wildcard $(REPO)/native/node-core/core-lite/tests/test_*.cpp)

# By BASENAME: the rule below puts every executable directly in `tests/`, and a
# path that kept the source's directories would be a target that is never built.
TEST_BINS := $(foreach t,$(COLLECTOR_TESTS) $(SUPERVISOR_TESTS) $(CORELITE_TESTS),$(OUT)/tests/$(notdir $(basename $(t))))

# CTest registers ONE MORE invocation than there are executables: an extra
# `add_test(NAME core_lite_shipped_profiles COMMAND test_core_lite_profile <two
# shipped profiles>)` in `core-lite/CMakeLists.txt`. Running each executable once
# with no arguments — which is all a loop over `TEST_BINS` can do — never
# validates a shipped profile at all, so a profile that stopped parsing would
# leave this green. It is run explicitly and counted separately.
SHIPPED_PROFILES := $(REPO)/native/node-core/core-lite/profiles/tokyo-vince.profile.json \
                    $(REPO)/native/node-core/core-lite/profiles/dell-fjd.profile.json

.PHONY: all tests run-tests manifest verify dist clean
.DEFAULT_GOAL := all

all: $(BINARIES) manifest

# THE RELEASE PAYLOAD. `release.py promote --artifact` copies a directory into an
# immutable `revisions/<revision>`, and the native verifier refuses any file the
# package did not declare — so the artifact handed to it is `bin/` plus
# `PACKAGE.json`, not this build root, which also carries `obj/` and `tests/`.
# Pointing the release path at the build root is how a revision ends up holding
# files nobody declared, and a revision cannot be edited after it is staged.
#
# The manifest is copied byte-for-byte: its digests are over `bin/`'s bytes, so a
# re-generated one would describe a different build.
dist: all
	@rm -rf $(OUT)/dist
	@mkdir -p $(OUT)/dist/bin
	@cp -p $(BINARIES) $(OUT)/dist/bin/
	@cp -p $(OUT)/PACKAGE.json $(OUT)/dist/
	@cp -p $(OUT)/ARTIFACT.json $(OUT)/dist/
	@echo "payload: $(OUT)/dist  (release.py promote --artifact $(OUT)/dist)"

# --- directories -----------------------------------------------------------
$(OUT)/obj $(OUT)/bin $(OUT)/tests:
	@mkdir -p $@

# --- the one compile rule ------------------------------------------------
# `$(2)` carries per-target defines; it is empty for library sources.
define compile_rule
$(call obj,$(1)): $(1) | $(OUT)/obj
	$$(CXX) $$(CXXFLAGS) $$(INCLUDES) $(2) -c $$< -o $$@
endef

$(foreach src,$(ALL_SRC),$(eval $(call compile_rule,$(REPO)/$(src),)))

# --- binaries -------------------------------------------------------------
$(BIN_NATIVE_CORE): $(call obj,$(REPO)/$(MAIN_NATIVE_CORE)) $(LIB_NODECORE) $(LIB_SUPERVISOR) | $(OUT)/bin
	$(CXX) $(CXXFLAGS) $^ -o $@
	chmod 0755 $@

$(BIN_SNAPSHOT): $(call obj,$(REPO)/$(MAIN_SNAPSHOT)) $(LIB_NODECORE) | $(OUT)/bin
	$(CXX) $(CXXFLAGS) $^ -o $@
	chmod 0755 $@

$(BIN_CORE_STATUS): $(call obj,$(REPO)/native/node-core/core-lite/src/status_main.cpp) $(LIB_CORELITE) $(LIB_SUPERVISOR) $(LIB_NODECORE) | $(OUT)/bin
	$(CXX) $(CXXFLAGS) $^ -o $@
	chmod 0755 $@

$(BIN_CORE_CHECK): $(call obj,$(REPO)/native/node-core/core-lite/src/profile_check.cpp) $(LIB_CORELITE) $(LIB_SUPERVISOR) $(LIB_NODECORE) | $(OUT)/bin
	$(CXX) $(CXXFLAGS) $^ -o $@
	chmod 0755 $@

# --- tests ----------------------------------------------------------------
define test_rule
$(OUT)/tests/$(notdir $(basename $(1))): $(call obj,$(1)) $(2) | $(OUT)/tests
	$$(CXX) $$(CXXFLAGS) $$^ -o $$@
	chmod 0755 $$@
endef

# Test sources need compile rules too; `ALL_SRC` is the shipped set only. The
# supervisor's carry the one define they read, emitted ONCE with it rather than
# defined and then overridden — a second recipe for one target is a make warning,
# and a warning on every build teaches people to stop reading the output.
$(foreach src,$(COLLECTOR_TESTS) $(CORELITE_TESTS),$(eval $(call compile_rule,$(src),)))
$(foreach src,$(SUPERVISOR_TESTS),$(eval $(call compile_rule,$(src),$(SUPERVISOR_SOURCE_DIR))))

$(foreach t,$(COLLECTOR_TESTS),$(eval $(call test_rule,$(t),$(LIB_NODECORE))))
$(foreach t,$(SUPERVISOR_TESTS),$(eval $(call test_rule,$(t),$(LIB_NODECORE) $(LIB_SUPERVISOR))))
$(foreach t,$(CORELITE_TESTS),$(eval $(call test_rule,$(t),$(LIB_CORELITE) $(LIB_SUPERVISOR) $(LIB_NODECORE))))

tests: $(TEST_BINS)

run-tests: $(TEST_BINS)
	@fail=0; \
	for t in $(TEST_BINS); do \
	  out=$$(cd $(REPO) && "$$t" 2>&1); code=$$?; \
	  summary=$$(printf '%s\n' "$$out" | sed '/^[[:space:]]*$$/d' | tail -n 1); \
	  if [ $$code -eq 0 ]; then status=PASS; else status=FAIL; fail=$$((fail+1)); fi; \
	  printf '%s %-30s %s\n' "$$status" "$$(basename $$t)" "$$summary"; \
	done; \
	summary=$$(cd $(REPO) && "$(OUT)/tests/test_profile" $(SHIPPED_PROFILES) 2>&1); \
	code=$$?; \
	if [ $$code -eq 0 ]; then status=PASS; else status=FAIL; fail=$$((fail+1)); fi; \
	printf '%s %-30s %s\n' "$$status" "core_lite_shipped_profiles" \
	  "$$(printf '%s\n' "$$summary" | sed '/^[[:space:]]*$$/d' | tail -n 1)"; \
	printf '\n%d test executables + 1 shipped-profile check, %d failing\n' \
	  "$(words $(TEST_BINS))" "$$fail"; \
	exit $$fail

# --- the manifest ---------------------------------------------------------
manifest: $(BINARIES)
	python3 -I $(REPO)/serverosd/package_manifest.py \
	  --out $(OUT) \
	  --commit '$(SOURCE_COMMIT)' \
	  --dirty '$(SOURCE_DIRTY)' \
	  --cxx '$(CXX)' \
	  --cxx-version '$(CXX_VERSION)' \
	  --cxxflags '$(CXXFLAGS)'

verify: all
	python3 -I $(REPO)/serverosd/verify_native_runtime.py $(OUT)

clean:
	@echo "nothing here deletes: remove $(OUT) yourself if that is what you meant"

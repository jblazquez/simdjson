#!/usr/bin/env python3
#
# Creates the amalgamated source files.
#

import sys
import os.path
import subprocess
import os
import re
import shutil
import datetime
from typing import Dict, Iterable, List, Literal, Optional, Protocol, Set, TextIO, Union, cast

if sys.version_info < (3, 0):
    sys.stdout.write("Sorry, requires Python 3.x or better\n")
    sys.exit(1)

SCRIPTPATH = os.path.dirname(os.path.abspath(sys.argv[0]))
PROJECTPATH = os.path.dirname(SCRIPTPATH)
print(f"SCRIPTPATH={SCRIPTPATH} PROJECTPATH={PROJECTPATH}")


print("We are about to amalgamate all simdjson files into one source file.")
print("See https://www.sqlite.org/amalgamation.html and https://en.wikipedia.org/wiki/Single_Compilation_Unit for rationale.")
if "AMALGAMATE_SOURCE_PATH" not in os.environ:
    AMALGAMATE_SOURCE_PATH = os.path.join(PROJECTPATH, "src")
else:
    AMALGAMATE_SOURCE_PATH = os.environ["AMALGAMATE_SOURCE_PATH"]
if "AMALGAMATE_INCLUDE_PATH" not in os.environ:
    AMALGAMATE_INCLUDE_PATH = os.path.join(PROJECTPATH, "include")
else:
    AMALGAMATE_INCLUDE_PATH = os.environ["AMALGAMATE_INCLUDE_PATH"]
if "AMALGAMATE_OUTPUT_PATH" not in os.environ:
    AMALGAMATE_OUTPUT_PATH = os.path.join(SCRIPTPATH)
else:
    AMALGAMATE_OUTPUT_PATH = os.environ["AMALGAMATE_OUTPUT_PATH"]

RelativeRoot = Literal['src','include']
RELATIVE_ROOTS: List[RelativeRoot] = ['src', 'include' ]
Implementation = Literal['arm64', 'fallback', 'haswell', 'icelake', 'ppc64', 'westmere']
IMPLEMENTATIONS: List[Implementation] = [ 'arm64', 'fallback', 'haswell', 'icelake', 'ppc64', 'westmere' ]
GENERIC_INCLUDE = "simdjson/generic"
GENERIC_SRC = "generic"
BUILTIN = "simdjson/builtin"
BUILTIN_BEGIN_H = f"{BUILTIN}/begin.h"
BUILTIN_END_H = f"{BUILTIN}/end.h"
IMPLEMENTATION_DETECTION_H = "simdjson/implementation_detection.h"

class SimdjsonFile:
    def __init__(self, repository: 'SimdjsonRepository', root: RelativeRoot, include_path: str):
        self.repository = repository
        self.root = root
        self.include_path = include_path
        self.includes: List[SimdjsonFile] = []
        self.included_from: Set[SimdjsonFile] = set()
        self.editor_only_includes: List[SimdjsonFile] = []
        self.editor_only_included_from: Set[SimdjsonFile] = set()
        self.processed: Optional[bool] = None

    def __str__(self):
        return self.include_path

    def __repr__(self):
        return self.include_path

    @property
    def project_relative_path(self):
        return f"{self.root}/{self.include_path}"

    @property
    def absolute_path(self):
        return os.path.join(self.repository.project_path, self.root, self.include_path)

    @property
    def is_generic(self):
        return self.include_path.startswith('generic/') or self.include_path.startswith('simdjson/generic/')

    @property
    def include_dir(self):
        return os.path.dirname(self.include_path)

    @property
    def filename(self):
        return os.path.basename(self.include_path)

    @property
    def implementation(self) -> Optional[Implementation]:
        match = re.search(f'(^|/)({"|".join(IMPLEMENTATIONS)})', self.include_path)
        if match:
            return cast(Implementation, str(match.group(2)))

    @property
    def free_dependency_file(self):
        if self.is_free_dependency_file:
            return None

        if self.implementation:
            # src/arm64.cpp, etc. -> generic/dependencies.h
            if self.include_dir == '':
                return self.repository["generic/dependencies.h"]

            # simdjson/arm64/ondemand.h
            if self.filename == 'ondemand.h':
                return self.repository["simdjson/generic/ondemand/dependencies.h"]

            # simdjson/arm64.h, simdjson/arm64/*.h
            else:
                return self.repository["simdjson/generic/dependencies.h"]

        if self.include_path.startswith('generic/') or self.include_path.startswith('simdjson/generic/'):
            return self.repository[f"{self.include_dir}/dependencies.h"]

        return None

    @property
    def is_amalgamator(self):
        if self.implementation:
            return self.root == 'src' or self.include_dir == 'simdjson' or self.filename == 'ondemand.h' or self.filename == 'implementation.h'
        else:
            return self.filename == 'amalgamated.h'

    # The file that is authorized to include this file in amalgamation. If this is blank, it's
    # generally because it *is* an amalgamator file.
    @property
    def amalgamator_file(self):
        # generic/dependencies.h and things like amd64.h must not be in dependencies
        if self.is_free_dependency_file or self.is_amalgamator or self.implementation:
            return None

        # generic/*.h -> generic/amalgamated.h
        # generic/stage1/*.h -> generic/stage1/amalgamated.h
        # simdjson/generic/*.h -> simdjson/generic/amalgamated.h
        # simdjson/generic/ondemand/*.h -> simdjson/generic/ondemand/amalgamated.h
        if self.is_generic:
            return self.repository[f"{self.include_dir}/amalgamated.h"]

        return None

    @property
    def is_free_dependency(self):
        return self.free_dependency_file is None

    @property
    def is_amalgamated(self):
        return not self.is_free_dependency

    @property
    def is_free_dependency_file(self):
        return self.filename == 'dependencies.h'

    def add_include(self, include: 'SimdjsonFile'):
        # If there's a place we have to put dependencies, figure out if this is valid to include
        if self.is_free_dependency:
            assert include.is_free_dependency or include.is_amalgamator, f"{self} cannot include {include} because it is an amalgamated file."
        else:
            assert not include.is_free_dependency, f"{self} cannot include {include} without #ifndef SIMDJSON_AMALGAMATED."
            # TODO make sure we only include amalgamated files that are guaranteed to be included with us (or before us)
            # if include.amalgamator_file:
            #     assert include.amalgamator_file == self, f"{self} cannot include {include}: it should be included from {include.amalgamator_file} instead."

        self.includes.append(include)
        include.included_from.add(self)

    def add_editor_only_include(self, include: 'SimdjsonFile'):
        assert self.is_amalgamated, f"Cannot use #ifndef SIMDJSON_AMALGAMATED in {self} because it is not an amalgamated file."
        if include.is_free_dependency:
            assert self.free_dependency_file, f"{self} cannot include {include} without #ifndef SIMDJSON_AMALGAMATED."
        # TODO make sure we only include amalgamated files that are guaranteed to be included with us (or before us)
        # elif include.amalgamator_file:
        #     assert self.is_amalgamated_before(self.amalgamator_file), f"{self} cannot include {include}: it should be included from {include.amalgamator_file} instead."

        self.editor_only_includes.append(include)
        include.editor_only_included_from.add(self)

    def validate_free_dependency_file(self):
        if self.is_free_dependency_file:
            extra_include_set = set(self.includes)
            for file in self.repository:
                if file.free_dependency_file == self:
                    for editor_only_include in file.editor_only_includes:
                        if editor_only_include.is_free_dependency:
                            assert editor_only_include in self.includes, f"{file} includes {editor_only_include}, but it is not included from {self}. It must be added to {self}."
                            if editor_only_include in extra_include_set:
                                extra_include_set.remove(editor_only_include)

            assert len(extra_include_set) == 0, f"{self} unnecessarily includes {extra_include_set}. They are not included in the corresponding amalgamated files."

class SimdjsonRepository:
    def __init__(self, project_path: str, relative_roots: List[RelativeRoot]):
        self.project_path = project_path
        self.relative_roots = relative_roots
        self.files: Dict[str, SimdjsonFile] = {}

    def validate_free_dependency_files(self):
        for file in self:
            file.validate_free_dependency_file()

    def __len__(self):
        return len(self.files)

    def __contains__(self, include_path: Union[str,SimdjsonFile]):
        if isinstance(include_path, SimdjsonFile):
            return include_path.include_path in self.files
        else:
            return include_path in self.files

    def __getitem__(self, include_path: str):
        if include_path not in self.files:
            self.files[include_path] = SimdjsonFile(self, self._included_filename_root(include_path), include_path)
        return self.files[include_path]

    def __iter__(self):
        return iter(self.files.values())

    def _included_filename_root(self, filename: str):
        result = None
        for relative_root in self.relative_roots:
            if os.path.exists(os.path.join(self.project_path, relative_root, filename)):
                assert result is None, "{file} exists in both {result} and {root}!"
                result = relative_root
        assert result, f"{filename} not found in {self.relative_roots}"
        return result


class Amalgamator:
    @classmethod
    def amalgamate(cls, output_path: str, file: str, roots: List[RelativeRoot], timestamp: str):
        print(f"Creating {output_path}")
        fid = open(output_path, 'w')
        print(f"/* auto-generated on {timestamp}. Do not edit! */", file=fid)
        amalgamator = cls(fid, SimdjsonRepository(PROJECTPATH, roots))
        amalgamator.maybe_write_file(amalgamator.repository[file], None, "")
        amalgamator.repository.validate_free_dependency_files()
        fid.close()

    def __init__(self, fid: TextIO, repository: SimdjsonRepository):
        self.fid = fid
        self.repository = repository
        self.builtin_implementation = False
        self.implementation: Optional[str] = None
        self.found_includes: Set[SimdjsonFile] = set()
        self.found_includes_per_amalgamation: Set[SimdjsonFile] = set()
        self.found_generic_includes: List[tuple[SimdjsonFile, str]] = []
        self.amalgamated_defined = False
        self.editor_only_region = False
        self.include_stack: List[SimdjsonFile] = []

    def maybe_write_file(self, file: SimdjsonFile, including_file: Optional[SimdjsonFile], else_line: str):
        if file.is_amalgamated:
            if file.is_generic:
                # Generic files get written out once per implementation in a well-defined order
                assert (file, self.implementation) not in self.found_generic_includes, f"generic file {file} included from {including_file} a second time for {self.implementation}!"
                assert self.implementation, file
                self.found_generic_includes.append((file, self.implementation))
            else:
                # Other amalgamated files, on the other hand, may only be included once per *amalgamation*
                if file not in self.found_includes_per_amalgamation:
                    self.found_includes_per_amalgamation.add(file)
        else:
            if file in self.found_includes:
                self.write(f"/* skipped duplicate {else_line} */")
                return
            self.found_includes.add(file)

        self.write(f"/* including {self.file_to_str(file)}: {else_line} */")
        self.write_file(file)

    def write(self, line: str):
        print(line, file=self.fid)

    def file_to_str(self, file: SimdjsonFile):
        if file.is_generic and file.is_amalgamated:
            assert self.implementation, file
            return f"{file} for {self.implementation}"
        return file

    def write_file(self, file: SimdjsonFile):
        # Detect cyclic dependencies
        assert file not in self.include_stack, f"Cyclic include: {self.include_stack} -> {file}"
        self.include_stack.append(file)

        file.processed = False

        self.write(f"/* begin file {self.file_to_str(file)} */")

        if file == BUILTIN_BEGIN_H:
            assert self.implementation is None, self.implementation
            assert not self.builtin_implementation, self.builtin_implementation
            self.builtin_implementation = True
            self.implementation = "SIMDJSON_BUILTIN_IMPLEMENTATION"

        assert not self.editor_only_region
        with open(file.absolute_path, 'r') as fid2:
            for line in fid2:
                line = line.rstrip('\n')

                # Ignore lines inside #ifndef SIMDJSON_AMALGAMATED
                if re.search(r'^#ifndef\s+SIMDJSON_AMALGAMATED\s*$', line):
                    assert file.is_amalgamated, f"{file} uses #ifndef SIMDJSON_AMALGAMATED but is not an amalgamated file!"
                    assert self.amalgamated_defined, f"{file} uses #ifndef SIMDJSON_AMALGAMATED without a prior #define SIMDJSON_AMALGAMATED: {self.include_stack}"
                    assert not self.editor_only_region, f"{file} uses #ifndef SIMDJSON_AMALGAMATED twice in a row"
                    self.editor_only_region = True

                # Handle ignored lines (and ending ignore blocks)
                end_ignore = re.search(r'^#endif\s*//\s*SIMDJSON_AMALGAMATED\s*$', line)
                if self.editor_only_region:
                    self.write(f"/* amalgamation skipped (editor-only): {line} */")

                    # Add the editor-only include so we can check dependencies.h for completeness later
                    included = re.search(r'^#include "([^"]*)"', line)
                    if included:
                        file.add_editor_only_include(self.repository[included.group(1)])
                    if end_ignore:
                        self.editor_only_region = False
                    continue

                assert not end_ignore, f"{file} has #endif // SIMDJSON_AMALGAMATED without #ifndef SIMDJSON_AMALGAMATED"

                # Handle #include lines
                included = re.search(r'^#include "([^"]*)"', line)
                if included:
                    # we explicitly include simdjson headers, one time each (unless they are generic, in which case multiple times is fine)
                    included_file = self.repository[included.group(1)]
                    file.add_include(included_file)
                    self.maybe_write_file(included_file, file, line)
                    continue

                # Handle defining and replacing SIMDJSON_IMPLEMENTATION
                defined = re.search(r'^#define\s+SIMDJSON_IMPLEMENTATION\s+(.+)$', line)
                if defined:
                    old_implementation = self.implementation
                    self.implementation = defined.group(1)
                    if old_implementation is None:
                        self.write(f'/* defining SIMDJSON_IMPLEMENTATION to "{self.implementation}" */')
                    else:
                        self.write(f'/* redefining SIMDJSON_IMPLEMENTATION from "{old_implementation}" to "{self.implementation}" */')
                elif re.search(r'^#undef\s+SIMDJSON_IMPLEMENTATION\s*$', line):
                    # Don't include #undef SIMDJSON_IMPLEMENTATION since we're handling it ourselves
                    self.write(f'/* undefining SIMDJSON_IMPLEMENTATION from "{self.implementation}" */')
                    self.implementation = None
                elif re.search(r'\bSIMDJSON_IMPLEMENTATION\b', line) and file.include_path != IMPLEMENTATION_DETECTION_H:
                    # copy the line, with SIMDJSON_IMPLEMENTATION replace to what it is currently defined to
                    assert self.implementation, f"Use of SIMDJSON_IMPLEMENTATION while not defined in {file}: {line}"
                    line = re.sub(r'\bSIMDJSON_IMPLEMENTATION\b',self.implementation,line)

                # Handle defining and undefining SIMDJSON_AMALGAMATED
                defined = re.search(r'^#define\s+SIMDJSON_AMALGAMATED\s*$', line)
                if defined:
                    assert not file.is_amalgamated, "SIMDJSON_AMALGAMATED defined in amalgamated file {file}! Not allowed."
                    assert not self.amalgamated_defined, f"{file} redefines SIMDJSON_AMALGAMATED"
                    self.amalgamated_defined = True
                    self.found_includes_per_amalgamation.clear()
                    self.write(f'/* defining SIMDJSON_AMALGAMATED */')
                elif re.search(r'^#undef\s+SIMDJSON_AMALGAMATED\s*$', line):
                    assert not file.is_amalgamated, "SIMDJSON_AMALGAMATED undefined in amalgamated file {file}! Not allowed."
                    assert self.amalgamated_defined, f"{file} undefines SIMDJSON_AMALGAMATED without defining it"
                    self.write(f'/* undefining SIMDJSON_AMALGAMATED */')
                    self.amalgamated_defined = False

                self.write(line)

            assert not self.editor_only_region, f"{file} ended without #endif // SIMDJSON_AMALGAMATED"

        self.write(f"/* end file {self.file_to_str(file)} */")

        if file.include_path == BUILTIN_BEGIN_H:
            # begin.h redefined SIMDJSON_IMPLEMENTATION multiple times
            assert self.builtin_implementation
            self.implementation = "SIMDJSON_BUILTIN_IMPLEMENTATION"
        elif file.include_path == BUILTIN_END_H:
            assert self.implementation is None
            assert self.builtin_implementation
            self.implementation = None

        file.processed = True

        self.include_stack.pop()

# Get the generation date from git, so the output is reproducible.
# The %ci specifier gives the unambiguous ISO 8601 format, and
# does not change with locale and timezone at time of generation.
# Forcing it to be UTC is difficult, because it needs to be portable
# between gnu date and busybox date.
try:
    proc = subprocess.run(['git', 'show', '-s', '--format=%ci', 'HEAD'],
                           stdout=subprocess.PIPE)
    print("the commandline is {}".format(proc.args))
    timestamp = proc.stdout.decode('utf-8').strip()
except:
    print("git not found, timestamp based on current time")
    timestamp = str(datetime.datetime.now())
print(f"timestamp is {timestamp}")

os.makedirs(AMALGAMATE_OUTPUT_PATH, exist_ok=True)
AMAL_H = os.path.join(AMALGAMATE_OUTPUT_PATH, "simdjson.h")
AMAL_C = os.path.join(AMALGAMATE_OUTPUT_PATH, "simdjson.cpp")
DEMOCPP = os.path.join(AMALGAMATE_OUTPUT_PATH, "amalgamate_demo.cpp")
README = os.path.join(AMALGAMATE_OUTPUT_PATH, "README.md")

Amalgamator.amalgamate(AMAL_H, "simdjson.h", ['include'], timestamp)
Amalgamator.amalgamate(AMAL_C, "simdjson.cpp", ['src', 'include'], timestamp)

# copy the README and DEMOCPP
if SCRIPTPATH != AMALGAMATE_OUTPUT_PATH:
  shutil.copy2(os.path.join(SCRIPTPATH,"amalgamate_demo.cpp"),AMALGAMATE_OUTPUT_PATH)
  shutil.copy2(os.path.join(SCRIPTPATH,"README.md"),AMALGAMATE_OUTPUT_PATH)

print("Done with all files generation.")

print(f"Files have been written to directory: {AMALGAMATE_OUTPUT_PATH}/")
print(subprocess.run(['ls', '-la', AMAL_C, AMAL_H, DEMOCPP, README],
                     stdout=subprocess.PIPE).stdout.decode('utf-8').strip())
print("Done with all files generation.")


#
# Instructions to create demo
#

print("\nGiving final instructions:")
with open(README) as r:
    for line in r:
        print(line)

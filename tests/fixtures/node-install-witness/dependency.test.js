import { strictEqual } from "node:assert";
import { test } from "node:test";

import { mark, version } from "rf-witness-dep";

test("the installed dependency is the pinned one, and it is the code that runs", () => {
  strictEqual(version, "1.0.0");
  strictEqual(mark("rf-s08-witness"), "rf-witness-dep 1.0.0 used by rf-s08-witness");
});

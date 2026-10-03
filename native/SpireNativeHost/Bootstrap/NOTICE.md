GameBootstrap.cs, OfflineLocalization.cs, MainLoopContext.cs and MemorySaveStore.cs derive from
Torch1230/CombatSolver commit 4d2c55069f2cf9f8c621631594857b412cf9660f
tools/OfflineSearchHarness, under the included MIT license.

Local change: remove the CombatSolver display-server override, so this data host
depends only on the installed game, GodotSharp and Harmony. These bootstrap
bypasses are version-specific and are reported in every export. They are not
a guarantee of native Godot gameplay equivalence. No game source or DLL is
distributed in this directory.

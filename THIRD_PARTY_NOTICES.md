# Third-party notices

SpireExact's original implementation in this archive is provided under the accompanying MIT license.

CombatSolver is a separate project by Torch1230 and contributors. Its license includes attribution to hotwords123 for portions derived from Random Foreseer. This archive does not bundle CombatSolver's full source tree or binaries. The source-fetch utility retrieves its original repository at a pinned commit and must retain its LICENSE and THIRD_PARTY_NOTICES.md.

The native probe is project integration code referencing CombatSolver and MegaCrit STS2 types. It is not a redistribution of the STS2 game assembly. The source-release build scope is recorded in release/BUILD_VALIDATION.json; historical holdout execution evidence is recorded separately in release/holdout-01/.

The standalone native host includes four MIT-licensed bootstrap files derived from CombatSolver's OfflineSearchHarness at the pinned commit. See native/SpireNativeHost/Bootstrap/NOTICE.md and LICENSE.CombatSolver. Its data-export path reads the supplied game DLL directly; campaign planning uses BeamAdvisor to load the separately fetched and built CombatSolver and OfflineSearchHarness. Game source obtained for local API investigation is not part of this source release.

CombatSolver repository: `https://github.com/Torch1230/CombatSolver`

Random Foreseer attribution source: CombatSolver `src/Engine/README.md` and `THIRD_PARTY_NOTICES.md`.

STS2, the game assets and game assemblies belong to their respective rightsholders. This project supplies neither game binaries nor assets.

The generated root-policy tiers in spire_exact/planning/policy_data.py come from the Spire Codex hosted public API. The file preserves its source hashes and generation rules; tools/build_policy_tiers.py is the generator. API terms: https://github.com/ptrlrd/spire-codex/blob/main/API_TERMS.md . The API terms and Spire Codex's separate source-code license are distinct; this project does not redistribute Spire Codex source code or game art.

The optional dashboard's generated Chinese name/option-title mappings preserve their provenance in dashboard/web/route_zh_data.js and tools/build_route_zh.py. Their sources include slaythespire2.net and the Chinese Huiji community wiki. The mappings are display references, not a redistribution of full game descriptions or artwork.

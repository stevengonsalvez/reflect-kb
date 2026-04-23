{
  description = "reflect-kb — universal cross-harness retrieval + learning KB for AI coding agents";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        python = pkgs.python311;
      in {
        # Placeholder package. Real derivation (bundling the CLI + nano-graphrag
        # + qmd + sentence-transformers) lands in a follow-up PR per the v4 spec.
        packages.default = pkgs.runCommand "reflect-kb-placeholder" { } ''
          mkdir -p $out/bin
          cat > $out/bin/reflect <<'EOF'
          #!/usr/bin/env bash
          echo "reflect-kb: scaffold only — CLI not yet implemented."
          echo "See https://github.com/stevengonsalvez/ai-coder-rules/blob/main/plans/reflect-v4-universal-install-spec.md"
          exit 64
          EOF
          chmod +x $out/bin/reflect
        '';

        devShells.default = pkgs.mkShell {
          packages = [
            python
            python.pkgs.pip
            pkgs.uv
            pkgs.git
            pkgs.gh
            pkgs.pre-commit
          ];

          shellHook = ''
            echo "reflect-kb dev shell (python=${python.version})"
            echo "See plans/reflect-v4-universal-install-spec.md for the install surface."
          '';
        };
      });
}

// Secret detector process for claude-pii-proxy (specs/001-pii-scrubbing-proxy/contracts/secret-detector.md).
//
// The rules load once; a fresh process per request would cost ~0.5 s each (research R7).
// Requests are JSON lines on stdin, one JSON line of findings per request on stdout.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"runtime/debug"

	"github.com/spf13/viper"
	"github.com/zricethezav/gitleaks/v8/config"
	"github.com/zricethezav/gitleaks/v8/detect"
)

const gitleaksModule = "github.com/zricethezav/gitleaks/v8"

type request struct {
	ID    int      `json:"id"`
	Texts []string `json:"texts"`
}

type finding struct {
	Rule        string   `json:"rule"`
	Secret      string   `json:"secret"`
	StartLine   int      `json:"start_line"`
	EndLine     int      `json:"end_line"`
	StartColumn int      `json:"start_column"`
	EndColumn   int      `json:"end_column"`
	Tags        []string `json:"tags"`
}

type response struct {
	ID       int         `json:"id"`
	Findings [][]finding `json:"findings"` // one list per request text
}

func fail(code int, what string, err error) {
	fmt.Fprintf(os.Stderr, "%s: %v\n", what, err)
	os.Exit(code)
}

// The proxy compares this against its pinned version, so it must come from the linked module, not a constant.
func gitleaksVersion() (string, error) {
	info, ok := debug.ReadBuildInfo()
	if !ok {
		return "", fmt.Errorf("no build info")
	}
	for _, dep := range info.Deps {
		if dep.Path == gitleaksModule {
			return dep.Version, nil
		}
	}
	return "", fmt.Errorf("%s not in build info", gitleaksModule)
}

func loadConfig(path string) (config.Config, error) {
	v := viper.New()
	v.SetConfigType("toml")
	v.SetConfigFile(path)
	if err := v.ReadInConfig(); err != nil {
		return config.Config{}, err
	}
	var vc config.ViperConfig
	if err := v.Unmarshal(&vc); err != nil {
		return config.Config{}, err
	}
	return vc.Translate()
}

func main() {
	if len(os.Args) != 2 {
		fail(2, "usage", fmt.Errorf("gitleaks-sidecar <rules.toml>"))
	}
	version, err := gitleaksVersion()
	if err != nil {
		fail(2, "version", err)
	}
	cfg, err := loadConfig(os.Args[1])
	if err != nil {
		fail(2, "config", err)
	}
	d := detect.NewDetector(cfg)
	d.IgnoreGitleaksAllow = true // a `gitleaks:allow` comment in user data must never hide a secret
	d.MaxDecodeDepth = 5         // NewDetector leaves it at 0; 5 is the CLI default

	out := json.NewEncoder(os.Stdout)
	if err := out.Encode(map[string]any{"ready": true, "gitleaks": version, "rules": len(cfg.Rules)}); err != nil {
		fail(4, "stdout", err)
	}
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 1<<20), 64<<20)
	for in.Scan() {
		var req request
		if err := json.Unmarshal(in.Bytes(), &req); err != nil {
			fail(3, "request", err)
		}
		res := response{ID: req.ID, Findings: make([][]finding, len(req.Texts))}
		for i, text := range req.Texts {
			res.Findings[i] = []finding{}
			for _, f := range d.DetectString(text) {
				res.Findings[i] = append(res.Findings[i], finding{
					f.RuleID, f.Secret, f.StartLine, f.EndLine, f.StartColumn, f.EndColumn, f.Tags,
				})
			}
		}
		if err := out.Encode(res); err != nil {
			fail(4, "stdout", err)
		}
	}
	if err := in.Err(); err != nil {
		fail(3, "stdin", err)
	}
}

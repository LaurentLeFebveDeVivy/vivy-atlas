package config

import (
	"fmt"
	"os"

	"gopkg.in/yaml.v3"
)

type Config struct {
	Database  Database  `yaml:"database"`
	Embedding Embedding `yaml:"embedding"`
}

type Database struct {
	URL string `yaml:"url"`
}

type Embedding struct {
	BaseURL     string `yaml:"base_url"`
	Model       string `yaml:"model"`
	QueryPrefix string `yaml:"query_prefix"`
	Dimension   int    `yaml:"dimension"`
}

func Load(path string) (*Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read config: %w", err)
	}
	var cfg Config
	if err := yaml.Unmarshal(raw, &cfg); err != nil {
		return nil, fmt.Errorf("parse config: %w", err)
	}

	if cfg.Database.URL == "" || cfg.Embedding.BaseURL == "" || cfg.Embedding.Model == "" {
		return nil, fmt.Errorf("config is missing required fields")
	}

	return &cfg, nil
}

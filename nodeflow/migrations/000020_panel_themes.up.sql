ALTER TABLE panel_settings
    DROP CONSTRAINT panel_settings_theme_check;

ALTER TABLE panel_settings
    ALTER COLUMN theme SET DEFAULT 'green',
    ADD CONSTRAINT panel_settings_theme_check
        CHECK (theme IN ('dark', 'green', 'rose', 'cyan', 'amber', 'system'));

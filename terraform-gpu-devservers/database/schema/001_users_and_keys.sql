-- API Users and Keys Schema
-- This table stores user information and API keys for authentication

-- Create users table if not exists
CREATE TABLE IF NOT EXISTS api_users (
    user_id SERIAL PRIMARY KEY,
    username VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT true
);

-- Create API keys table
CREATE TABLE IF NOT EXISTS api_keys (
    key_id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES api_users(user_id)
        ON DELETE CASCADE,
    key_hash VARCHAR(128) NOT NULL UNIQUE,
    key_prefix VARCHAR(16) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE,
    last_used_at TIMESTAMP WITH TIME ZONE,
    is_active BOOLEAN DEFAULT true,
    description TEXT
);

-- Create indexes for faster lookups
-- Index on api_keys.key_hash (for API key verification)
CREATE INDEX IF NOT EXISTS idx_api_keys_hash
    ON api_keys(key_hash)
    WHERE is_active = true;

-- Index on api_keys.user_id (for listing user's keys)
CREATE INDEX IF NOT EXISTS idx_api_keys_user_id
    ON api_keys(user_id)
    WHERE is_active = true;

-- Index on api_keys.expires_at (for cleanup queries)
CREATE INDEX IF NOT EXISTS idx_api_keys_expires_at
    ON api_keys(expires_at)
    WHERE is_active = true AND expires_at IS NOT NULL;

-- Index on api_users.username (for login lookups)
CREATE INDEX IF NOT EXISTS idx_api_users_username
    ON api_users(username);


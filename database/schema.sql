-- ============================================================================
-- DOCUMENTO DE REFERENCIA Y ARQUITECTURA (MYSQL 8+)
-- ADVERTENCIA: NO EJECUTAR ESTE SCRIPT EN ENTORNOS CON ALEMBIC INICIALIZADO.
-- La única fuente de verdad para la estructura y evolución del esquema de 
-- base de datos es Alembic (backend/migrations/versions/).
-- Este archivo se preserva exclusivamente como referencia técnica de DDL en MySQL.
-- ============================================================================
-- BASE DE DATOS: FarmhouseWhatsAppCenter
-- MOTOR: MySQL 8.0+ / InnoDB / utf8mb4
-- ============================================================================

CREATE DATABASE IF NOT EXISTS `FarmhouseWhatsAppCenter`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE `FarmhouseWhatsAppCenter`;

-- ----------------------------------------------------------------------------
-- 1. TABLA: branches (Sucursales Oficiales de Farmhouse)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `branches` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `name` VARCHAR(100) NOT NULL UNIQUE,
    `code` VARCHAR(50) NOT NULL UNIQUE,
    `color` VARCHAR(20) DEFAULT '#16a34a',
    `active` TINYINT(1) NOT NULL DEFAULT 1,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_branches_name` (`name`),
    INDEX `idx_branches_code` (`code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- 2. TABLA: users (Agentes, Supervisores y Administradores)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `users` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `username` VARCHAR(50) NOT NULL UNIQUE,
    `name` VARCHAR(150) NOT NULL,
    `email` VARCHAR(150) NULL,
    `password_hash` VARCHAR(255) NOT NULL,
    `role` VARCHAR(50) NOT NULL DEFAULT 'agent',
    `branch_id` INT NULL,
    `avatar_url` VARCHAR(255) NULL,
    `active` TINYINT(1) NOT NULL DEFAULT 1,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_users_username` (`username`),
    INDEX `idx_users_role` (`role`),
    CONSTRAINT `fk_users_branch` FOREIGN KEY (`branch_id`) REFERENCES `branches` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- 3. TABLA: devices (Hardware / Terminales Autorizados)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `devices` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `device_id` VARCHAR(50) NOT NULL UNIQUE,
    `name` VARCHAR(150) NOT NULL,
    `device_type` VARCHAR(50) NOT NULL,
    `branch_id` INT NOT NULL,
    `assigned_user_id` INT NULL,
    `status` VARCHAR(50) NOT NULL DEFAULT 'active',
    `ip_address` VARCHAR(50) NULL,
    `last_seen` DATETIME NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_devices_device_id` (`device_id`),
    INDEX `idx_devices_status` (`status`),
    CONSTRAINT `fk_devices_branch` FOREIGN KEY (`branch_id`) REFERENCES `branches` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_devices_user` FOREIGN KEY (`assigned_user_id`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- 4. TABLA: contacts (Clientes y Contactos de WhatsApp)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `contacts` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `name` VARCHAR(150) NOT NULL,
    `phone` VARCHAR(50) NOT NULL UNIQUE,
    `avatar_url` VARCHAR(255) NULL,
    `notes` TEXT NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `last_interaction` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `deleted_at` DATETIME NULL,
    INDEX `idx_contacts_phone` (`phone`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- 5. TABLA: conversations (Conversaciones de Atención)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `conversations` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `customer_id` INT NOT NULL,
    `branch_id` INT NULL,
    `assigned_user_id` INT NULL,
    `status` VARCHAR(50) NOT NULL DEFAULT 'new',
    `delivery_type` VARCHAR(20) NULL,
    `payment_method` VARCHAR(20) NULL,
    `last_branch_prompt_at` DATETIME NULL,
    `automation_paused` TINYINT(1) NOT NULL DEFAULT 0,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `deleted_at` DATETIME NULL,
    `deleted_by` INT NULL,
    INDEX `idx_conversations_status` (`status`),
    INDEX `idx_conversations_branch` (`branch_id`),
    CONSTRAINT `fk_conversations_customer` FOREIGN KEY (`customer_id`) REFERENCES `contacts` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_conversations_branch` FOREIGN KEY (`branch_id`) REFERENCES `branches` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_conversations_user` FOREIGN KEY (`assigned_user_id`) REFERENCES `users` (`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_conversations_deleter` FOREIGN KEY (`deleted_by`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- 6. TABLA: messages (Historial de Mensajes y Notas Internas)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `messages` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `conversation_id` INT NOT NULL,
    `direction` VARCHAR(20) NOT NULL,
    `sender_type` VARCHAR(20) NOT NULL,
    `sender_id` INT NULL,
    `content` TEXT NOT NULL,
    `is_internal` TINYINT(1) NOT NULL DEFAULT 0,
    `whatsapp_message_id` VARCHAR(100) NULL UNIQUE,
    `status` VARCHAR(20) NOT NULL DEFAULT 'sent',
    `error_detail` VARCHAR(500) NULL,
    `media_url` VARCHAR(500) NULL,
    `media_type` VARCHAR(20) NULL,
    `media_mime_type` VARCHAR(100) NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `deleted_at` DATETIME NULL,
    `deleted_by` INT NULL,
    INDEX `idx_messages_conversation` (`conversation_id`),
    INDEX `idx_messages_created_at` (`created_at`),
    INDEX `idx_messages_whatsapp_id` (`whatsapp_message_id`),
    CONSTRAINT `fk_messages_conversation` FOREIGN KEY (`conversation_id`) REFERENCES `conversations` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_messages_sender` FOREIGN KEY (`sender_id`) REFERENCES `users` (`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_messages_deleter` FOREIGN KEY (`deleted_by`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- 7. TABLA: orders (Comandas y Pedidos Vinculados)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `orders` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `order_code` VARCHAR(50) NOT NULL UNIQUE,
    `conversation_id` INT NOT NULL,
    `branch_id` INT NOT NULL,
    `order_type` VARCHAR(50) NOT NULL DEFAULT 'delivery',
    `status` VARCHAR(50) NOT NULL DEFAULT 'en_proceso',
    `subtotal` DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
    `delivery_cost` DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
    `tax` DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
    `total` DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
    `items_json` TEXT NULL,
    `created_by` INT NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NULL,
    `expires_at` DATETIME NULL,
    `deleted_at` DATETIME NULL,
    INDEX `idx_orders_code` (`order_code`),
    INDEX `idx_orders_status` (`status`),
    CONSTRAINT `fk_orders_conversation` FOREIGN KEY (`conversation_id`) REFERENCES `conversations` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_orders_branch` FOREIGN KEY (`branch_id`) REFERENCES `branches` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_orders_creator` FOREIGN KEY (`created_by`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- DATOS SEMILLA INICIALES (SEED DATA IDEMPOTENTE)
-- ============================================================================

-- 1. Sucursales Oficiales de Farmhouse Panamá
INSERT IGNORE INTO `branches` (`id`, `code`, `name`, `color`, `active`) VALUES
(1, 'CDE', 'Costa del Este', '#16a34a', 1),
(2, 'SF', 'San Francisco', '#0d9488', 1),
(3, 'CLY', 'Clayton', '#d97706', 1),
(4, 'OBR', 'Obarrio', '#2563eb', 1),
(5, 'VP', 'Via Porras', '#9333ea', 1),
(6, 'CAT', 'Catering', '#e11d48', 1);

-- 2. Usuario Administrador General Inicial (Password inicial: Admin123!)
INSERT IGNORE INTO `users` (`id`, `username`, `name`, `email`, `password_hash`, `role`, `branch_id`, `active`) VALUES
(1, 'admin', 'Administrador Farmhouse', 'admin@farmhouse.pa', '$2b$12$6t3rQ95eA0mIge6c7Ff3yeBvP0eZ8mEaB8lM1u6UfL7c7Jm3N9K7W', 'admin', NULL, 1);


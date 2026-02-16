IF OBJECT_ID('dbo.routine_task_child', 'U') IS NOT NULL
    DROP TABLE dbo.routine_task_child;

IF OBJECT_ID('dbo.routine_task', 'U') IS NOT NULL
    DROP TABLE dbo.routine_task;

CREATE TABLE dbo.routine_task (
    task_no INT IDENTITY(1,1) NOT NULL,
    frequency NVARCHAR(16) NOT NULL,
    half_year TINYINT NULL,
    due_date DATE NULL,
    start_month CHAR(7) NOT NULL,
    department_cd NVARCHAR(10) NULL,
    end_month CHAR(7) NOT NULL,
    [year] INT NOT NULL,
    quarter CHAR(2) NOT NULL,
    [month] INT NOT NULL,
    week_num INT NULL,
    assignee NVARCHAR(256) NULL,
    task_kind NVARCHAR(16) NOT NULL DEFAULT N'個人',
    registrant NVARCHAR(64) NULL,
    status NVARCHAR(16) NOT NULL,
    title NVARCHAR(128) NOT NULL,
    attachment_link NVARCHAR(256) NULL,
    summary NVARCHAR(MAX) NULL,
    is_deleted BIT NOT NULL DEFAULT 0,
    deleted_at DATETIME2(3) NULL,
    created_at DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_routine_task PRIMARY KEY CLUSTERED (task_no),
    CONSTRAINT CK_routine_task_task_kind CHECK (task_kind IN (N'グループ', N'個人'))
);

CREATE TABLE dbo.routine_task_child (
    record_no INT IDENTITY(1,1) NOT NULL,
    task_no INT NOT NULL,
    routine_no INT NOT NULL,
    due_date DATE NULL,
    title NVARCHAR(128) NULL,
    assignee NVARCHAR(256) NULL,
    status NVARCHAR(16) NOT NULL,
    summary NVARCHAR(MAX) NULL,
    is_deleted BIT NOT NULL DEFAULT 0,
    deleted_at DATETIME2(3) NULL,
    created_at DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_routine_task_child PRIMARY KEY CLUSTERED (record_no),
    CONSTRAINT FK_routine_child_parent FOREIGN KEY (task_no) REFERENCES dbo.routine_task(task_no)
);

DBCC CHECKIDENT ('dbo.routine_task', RESEED, 0);
DBCC CHECKIDENT ('dbo.routine_task_child', RESEED, 0);

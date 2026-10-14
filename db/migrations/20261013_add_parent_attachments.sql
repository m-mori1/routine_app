IF OBJECT_ID('dbo.routine_task_attachment', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.routine_task_attachment (
        attachment_id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        task_no INT NOT NULL,
        filename NVARCHAR(255) NOT NULL,
        file_size INT NOT NULL,
        content VARBINARY(MAX) NOT NULL,
        uploaded_by NVARCHAR(256) NULL,
        created_at DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
        is_deleted BIT NOT NULL DEFAULT 0,
        deleted_at DATETIME2(3) NULL,
        CONSTRAINT FK_routine_attachment_parent FOREIGN KEY (task_no)
            REFERENCES dbo.routine_task(task_no),
        CONSTRAINT CK_routine_attachment_size CHECK (
            file_size > 0 AND file_size <= 10485760 AND DATALENGTH(content) = file_size
        )
    );
    CREATE INDEX IX_routine_attachment_parent
        ON dbo.routine_task_attachment(task_no, is_deleted);
END;

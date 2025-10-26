"""Check database tables and schema."""
import asyncio
from sqlalchemy import text
from src.db.session import get_session


async def check_tables():
    """Check what tables exist in the database."""
    async with get_session() as session:
        # List all tables
        result = await session.execute(
            text("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name;
            """)
        )
        tables = result.fetchall()

        print("Existing tables in database:")
        print("=" * 50)
        for table in tables:
            print(f"  - {table[0]}")

        print("\n" + "=" * 50)
        print(f"Total tables: {len(tables)}\n")

        # Check backtest_runs table structure if it exists
        if any("backtest_runs" in str(t[0]) for t in tables):
            print("Backtest_runs table columns:")
            print("=" * 50)
            result = await session.execute(
                text("""
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_name = 'backtest_runs'
                    ORDER BY ordinal_position;
                """)
            )
            columns = result.fetchall()
            for col in columns:
                print(f"  - {col[0]}: {col[1]} (nullable: {col[2]})")

        # Check performance_metrics table structure if it exists
        if any("performance_metrics" in str(t[0]) for t in tables):
            print("\nPerformance_metrics table columns:")
            print("=" * 50)
            result = await session.execute(
                text("""
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_name = 'performance_metrics'
                    ORDER BY ordinal_position;
                """)
            )
            columns = result.fetchall()
            for col in columns:
                print(f"  - {col[0]}: {col[1]} (nullable: {col[2]})")

        # Check if old market_data table exists
        if any("market_data" in str(t[0]) for t in tables):
            print("\n⚠️  Old market_data table found - may need cleanup")
            result = await session.execute(
                text("SELECT COUNT(*) FROM market_data")
            )
            count = result.scalar()
            print(f"   Rows in market_data: {count}")


if __name__ == "__main__":
    asyncio.run(check_tables())

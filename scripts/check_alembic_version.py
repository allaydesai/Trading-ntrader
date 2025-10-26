"""Check what migration version is in the database."""
import asyncio
from sqlalchemy import text
from src.db.session import get_session


async def check_version():
    """Check alembic version in database."""
    async with get_session() as session:
        result = await session.execute(text("SELECT version_num FROM alembic_version"))
        version = result.scalar()
        print(f"Database migration version: {version}")


if __name__ == "__main__":
    asyncio.run(check_version())

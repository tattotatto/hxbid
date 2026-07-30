"""Temporary script to test PostgreSQL connectivity with actual credentials."""
import asyncio
import asyncpg


async def main():
    try:
        conn = await asyncpg.connect(
            user="hongxi",
            password="hongxi123",
            host="192.168.50.38",
            port=5432,
            database="hongxi_bid",
        )
        result = await conn.fetchval("SELECT 1")
        print(f"Connection OK: {result}")

        # Check current columns in bid_projects
        cols = await conn.fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'bid_projects' ORDER BY ordinal_position"
        )
        print("Current bid_projects columns:")
        for col in cols:
            print(f"  {col['column_name']}: {col['data_type']}")

        await conn.close()
    except Exception as e:
        print(f"Connection failed: {type(e).__name__}: {e}")


asyncio.run(main())

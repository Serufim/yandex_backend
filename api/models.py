from aiopg import sa
from sqlalchemy import (
    MetaData, Table, Column, ForeignKey,
    Integer, String, Date
)

metadata = MetaData()

imports = Table('imports', metadata,
                Column('id', Integer, primary_key=True, autoincrement=True))

relatives = Table('relatives', metadata,
                  Column('id', Integer, primary_key=True, autoincrement=True),
                  Column('citizen_id', Integer, ForeignKey('citizens.id'), nullable=False),
                  Column('relative_id', Integer, ForeignKey('citizens.id'), nullable=False)
                  )

citizens = Table('citizens', metadata,
                 Column('id', Integer, primary_key=True, autoincrement=True),
                 Column('citizen_id', Integer, nullable=False),
                 Column('town', String(255), nullable=False),
                 Column('street', String(255), nullable=False),
                 Column('building', String(255), nullable=False),
                 Column('apartment', Integer, nullable=False),
                 Column('building', String(255), nullable=False),
                 Column('name', String(255), nullable=False),
                 Column('birth_date', Date, nullable=False),
                 Column('gender', String(6), nullable=False),
                 Column('import_id', Integer, ForeignKey('imports.id'), nullable=False),
                 )


async def init_pg(app):
    conf = app['config']['postgres']
    engine = await sa.create_engine(
        database=conf['database'],
        user=conf['user'],
        password=conf['password'],
        host=conf['host'],
        port=conf['port'],
        minsize=conf['minsize'],
        maxsize=conf['maxsize'],
    )
    app['db'] = engine


async def close_pg(app):
    app['db'].close()
    await app['db'].wait_closed()
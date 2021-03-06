import datetime

from aiopg import sa
from sqlalchemy import (
    MetaData, Table, Column, ForeignKey,
    Integer, String, Date, select, or_,
    and_, func, alias, exists
)
from sqlalchemy.dialects.postgresql import insert

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
                 Column('town', String(256), nullable=False),
                 Column('street', String(256), nullable=False),
                 Column('building', String(256), nullable=False),
                 Column('apartment', Integer, nullable=False),
                 Column('name', String(256), nullable=False),
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


async def insert_import(conn, new_citizens, new_relatives) -> Integer:
    """Добавлеие импорта с горожанами"""
    async with conn.begin():
        # Cначала вставляем запись в таблицу imports и получаем id
        result = await conn.execute(imports.insert().values().returning(imports.c.id))
        import_id = await result.scalar()

        for new_citizen in new_citizens:
            new_citizen['import_id'] = import_id

        # Фишка постгреса returning в insert
        result = await conn.execute(
            insert(citizens).returning(citizens.c.id, citizens.c.citizen_id).values(
                new_citizens).on_conflict_do_nothing())

        inserted_citizens = await result.fetchall()
        citizen_mapper = {item[1]: item[0] for item in inserted_citizens}

        values = []

        for new_citizen in new_citizens:
            for relative in new_relatives[new_citizen['citizen_id']]:
                values.append({
                    'citizen_id': citizen_mapper[new_citizen['citizen_id']],
                    'relative_id': citizen_mapper[relative]})
        if values:
            await conn.execute(insert(relatives).returning(relatives.c.id).values(values).on_conflict_do_nothing())
    return import_id


async def delete_relatives_for_citizen(conn, citizen_id, citizen_relatives_mapper, relatives_for_deleting):
    """Удаляем родственников из списка"""
    deleted_relatives = {citizen_relatives_mapper[i] for i in relatives_for_deleting}

    await conn.execute(relatives.delete().where(
        or_(and_(relatives.c.citizen_id == citizen_id,
                 relatives.c.relative_id.in_(deleted_relatives)),
            and_(relatives.c.citizen_id.in_(deleted_relatives),
                 relatives.c.relative_id == citizen_id)),
    ))


async def add_relatives_for_citizen(conn, import_id, citizen_id, relatives_for_adding):
    """Добавляем родственников из списка"""
    added_relatives_id = await conn.execute(
        select([citizens.c.id, citizens.c.citizen_id])
        .where(citizens.c.citizen_id.in_(list(relatives_for_adding)))
        .where(citizens.c.import_id == import_id))
    a = await added_relatives_id.fetchall()

    added_relatives_mapper = {i[1]: i[0] for i in a}
    added_relatives = {added_relatives_mapper[i] for i in relatives_for_adding}
    values = []

    for relative_id in added_relatives:
        values.append({"citizen_id": citizen_id, "relative_id": relative_id})
        values.append({"citizen_id": relative_id, "relative_id": citizen_id})

    await conn.execute(insert(relatives).values(values))


async def update_citizen_data(conn, import_id, citizen_id, citizen_data, updating_relatives):
    """Обновление Горожанина"""
    async with conn.begin():
        result = await conn.execute(
            citizens.update().returning(citizens.c.id).where(citizens.c.citizen_id == citizen_id).where(
                citizens.c.import_id == import_id).values(citizen_data))
        citizen_id = await result.scalar()

        if updating_relatives:
            await update_citizen_relatives(conn, import_id, citizen_id, updating_relatives)

        return await get_citizen_by_id(conn, citizen_id)


async def update_citizen_relatives(conn, import_id, citizen_id, updating_relatives):
    """Обновляем родственников пользователя"""
    relative_id = await conn.execute(
        select([citizens.c.id, citizens.c.citizen_id]).where(relatives.c.citizen_id == citizen_id)
        .where(relatives.c.relative_id == citizens.c.id)
        .where(citizens.c.import_id == import_id))

    citizen_relatives = await relative_id.fetchall()
    citizen_relatives_set = {i[1] for i in citizen_relatives}
    citizen_relatives_mapper = {i[1]: i[0] for i in citizen_relatives}
    deleted_relatives = citizen_relatives_set - updating_relatives
    added_relatives = updating_relatives - citizen_relatives_set

    if deleted_relatives:
        await delete_relatives_for_citizen(conn, citizen_id, citizen_relatives_mapper, deleted_relatives)

    if added_relatives:
        await add_relatives_for_citizen(conn, import_id, citizen_id, added_relatives)


async def get_citizen_by_id(conn, citizen_id):
    """Возвращаем пользователя по id"""
    citizen_relatives = alias(citizens)
    select_fields = [citizens.c.citizen_id,
                     citizens.c.town,
                     citizens.c.street,
                     citizens.c.building,
                     citizens.c.apartment,
                     citizens.c.name,
                     citizens.c.birth_date,
                     citizens.c.gender]

    my_genius_join = citizens.outerjoin(relatives, citizens.c.id == relatives.c.relative_id) \
        .outerjoin(citizen_relatives, citizen_relatives.c.id == relatives.c.citizen_id)

    result = await conn.execute(select([*select_fields,
                                        func.array_agg(citizen_relatives.c.citizen_id).label("relatives"),
                                        ])
                                .select_from(my_genius_join)
                                .where(citizens.c.id == citizen_id)
                                .group_by(*select_fields))

    citizen_item = await result.fetchone()
    return citizen_item


async def get_import(conn, import_id):
    """Получаем всех пользователей в импорте"""
    citizen_relatives = alias(citizens)
    select_fields = [citizens.c.citizen_id,
                     citizens.c.town,
                     citizens.c.street,
                     citizens.c.building,
                     citizens.c.apartment,
                     citizens.c.name,
                     citizens.c.birth_date,
                     citizens.c.gender]

    my_genius_join = citizens.outerjoin(relatives, citizens.c.id == relatives.c.relative_id) \
        .outerjoin(citizen_relatives, citizen_relatives.c.id == relatives.c.citizen_id)

    result = await conn.execute(select([*select_fields,
                                        func.array_agg(citizen_relatives.c.citizen_id).label("relatives"),
                                        ])
                                .select_from(my_genius_join)
                                .where(citizens.c.import_id == import_id)
                                .group_by(*select_fields))

    import_items = await result.fetchall()

    return import_items


async def get_citizens_birthdays(conn, import_id):
    """Получаем пользователей и дни рождения их родственников"""
    citizen_relatives = alias(citizens)

    my_genius_join = citizens.outerjoin(relatives, citizens.c.id == relatives.c.relative_id) \
        .outerjoin(citizen_relatives, citizen_relatives.c.id == relatives.c.citizen_id)

    result = await conn.execute(select([citizens.c.citizen_id,
                                        func.array_agg(func.date_part('month', citizen_relatives.c.birth_date)).label(
                                            "birthdays"),
                                        ])
                                .select_from(my_genius_join)
                                .where(citizens.c.import_id == import_id)
                                .group_by(citizens.c.citizen_id))

    birthdays = await result.fetchall()

    return birthdays


async def get_percentiles(conn, import_id):
    """Считаем перентили (Я на постгресе смог посчитать, нно там не совпадает с numpy)"""
    citizen_relatives = alias(citizens)

    my_genius_join = citizens.outerjoin(relatives, citizens.c.id == relatives.c.relative_id) \
        .outerjoin(citizen_relatives, citizen_relatives.c.id == relatives.c.citizen_id)

    result = await conn.execute(select([citizens.c.town,
                                        func.array_agg(
                                            func.date_part('year', datetime.datetime.utcnow()) -
                                            func.date_part('year', citizens.c.birth_date))
                                       .label("birthdays"),
                                        ])
                                .select_from(my_genius_join)
                                .where(citizens.c.import_id == import_id)
                                .group_by(citizens.c.town))
    percentiles = await result.fetchall()

    return percentiles


async def check_relatives(conn, import_id, relatives):
    """Проверяем существование id родственников из спика relatives"""
    result = await conn.execute(select([func.count()])
                                .where(citizens.c.import_id == import_id)
                                .where(citizens.c.citizen_id.in_(relatives)))

    relatives_count = await result.scalar()

    if relatives_count != len(relatives):
        return False
    return True


async def check_citizen(conn, import_id, citizen_id):
    """Проверяем существование горожанина по id импорта и его id"""
    result = await conn.execute(
        select([exists().where(and_(citizens.c.citizen_id == citizen_id, citizens.c.import_id == import_id))]))

    return await result.scalar()


async def check_import(conn, import_id):
    """Проверяем существование импорта по id"""
    result = await conn.execute(
        select([exists().where(citizens.c.import_id == import_id)]))

    return await result.scalar()

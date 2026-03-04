import bcrypt

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def hours_list_to_mask(hours: list[str]) -> int:
    """
    Convierte ["1","2","6"] → bitmask.
    'Todas' → 0b1111111 (127)
    """
    if "Todas" in hours:
        return 127
    
    mapping = {"1":0,"2":1,"3":2,"RECREO":3,"4":4,"5":5,"6":6}
    mask = 0
    for h in hours:
        if h in mapping:
            mask |= (1 << mapping[h])
    return mask
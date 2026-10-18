from pydantic import BaseModel, ConfigDict


class FirmOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    uuid: str
    name: str
    balance_cents: int

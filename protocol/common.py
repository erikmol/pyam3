import crcmod.predefined
crc = crcmod.predefined.mkPredefinedCrcFun("crc-8-maxim")

class Protocol:
    def __init__(self, command:dict)->None:
        self.name = command['name']
        self.major = command['major']
        self.minor = command['minor']
        self.request_data_type = command["requestType"]
        self.response_data_type = command["responseType"]
        self.request_length = command["requestLength"]
        self.response_length = command["responseLength"]
        self.transaction_id: int | None = None

    def prepare_payload_data(self, **kwargs) -> bytearray:
        payload_data = bytearray()

        if self.request_data_type is not None:
            for request_name, request_type in self.request_data_type.items():
                if request_name not in kwargs:
                    raise ValueError(
                        f"Missing request parameter: {request_name} for command {self.name} ({self.major}, {self.minor})"
                    )

                if request_type == "uint32":
                    payload_data += kwargs[request_name].to_bytes(4, byteorder="little")
                elif request_type == "uint16":
                    payload_data += kwargs[request_name].to_bytes(2, byteorder="little")
                elif request_type == "uint8":
                    payload_data += kwargs[request_name].to_bytes(1, byteorder="little")
                else:
                    raise ValueError("Unknown request type: " + self.request_data_type)
                
        if not len(payload_data) == self.request_length:
            raise ValueError(
                f"Payload length mismatch for command {self.name} ({self.major}, {self.minor}): "
                f"Expected {self.request_length}, got {len(payload_data)}"
            )
        return payload_data
    
    def parse_data(self, data:bytearray)-> dict | None:
        if self.response_length == 0:
            return None
        response = {}
        dpos = 0
        for name, dtype in self.response_data_type.items():
            if (dtype == "tUnixTime") or (dtype == "uint32"):
                if dpos + 4 > len(data):
                    raise ValueError(
                        f"Response too short: need 4 bytes at offset {dpos} for field '{name}', but data is only {len(data)} bytes"
                    )
                response[name] = int.from_bytes(
                    data[dpos : dpos + 4], byteorder="little"
                )
                dpos += 4
            elif (dtype == "uint16") or (dtype == "sint16"):
                if dpos + 2 > len(data):
                    raise ValueError(
                        f"Response too short: need 2 bytes at offset {dpos} for field '{name}', but data is only {len(data)} bytes"
                    )
                signed = dtype == "sint16"
                response[name] = int.from_bytes(
                    data[dpos : dpos + 2],
                    byteorder="little",
                    signed=signed,
                )
                dpos += 2
            elif (dtype == "uint8") or (dtype == "bool"):
                if dpos >= len(data):
                    raise ValueError(
                        f"Response too short: expected byte at offset {dpos} for field '{name}', but data is only {len(data)} bytes"
                    )
                response[name] = data[dpos]
                dpos += 1
            else:
                raise ValueError("Unknown data type: " + dtype)
        if dpos != len(data):
            raise ValueError(
                "Data length mismatch. Read %d bytes of %d"
                % (dpos, len(data))
            )
        return response

import json
from importlib.resources import files
from protocol.simple import SimpleProtocol
from protocol.extended import ExtendedProtocol
from binascii import hexlify

class Commands:
    """
    Class to load all commands and do some postprocessing.
    """
    def __init__(self):
        with files("protocol").joinpath("commands.json").open("r") as file:
            self.commands = json.load(file)

            # Loop over the command dict to put the name in the dict as well:
            for command in self.commands:
                # Add the name to the command dict
                self.commands[command]["name"] = command

                if "event" not in self.commands[command]:
                    self.commands[command]["event"] = 0

                for x in ['response', 'request']:
                    if f"{x}Type" not in self.commands[command]:
                        self.commands[command][f"{x}Length"] = 0
                        self.commands[command][f"{x}Type"] = None
                    else:
                        # Always wrap in a dict 
                        if not isinstance(self.commands[command][f"{x}Type"], dict):
                            self.commands[command][f"{x}Type"] = {
                                x: self.commands[command][f"{x}Type"]
                            }
                        response_length = 0
                        # Count length 
                        for name, type in self.commands[command][f"{x}Type"].items():
                            if type.endswith("int32") or type == "tUnixTime":
                                response_length += 4
                            elif type.endswith("int16"):
                                response_length += 2
                            elif type == "uint8" or type == "bool":
                                response_length += 1
                            else:
                                raise ValueError("Unknown type: " + type)
                        self.commands[command][f"{x}Length"] = response_length
    def get_event_command_from_major_minor(self, major: int, minor: int) -> dict:
        """
        Get a command from the major and minor version.
        """
        for command in self.commands.values():
            if command["major"] == major and command["minor"] == minor and command["event"] == 1:
                return command
        raise ValueError(f"Event command with major {major} and minor {minor} not found.")

                

commands_class = Commands()
commands = commands_class.commands

def create_command(command_name: str):
    """
    Factory function to create a command object based on the command name.
    This function will look up the command in the commands.json file and create
    an instance of the appropriate command class.
    """
    if command_name not in commands:
        raise ValueError(f"Command '{command_name}' not found in commands.json")
    else:
        command = commands[command_name]
    
    if command["major"] < 0x7F:
        # This is a simple command
        return SimpleProtocol(command)
    else:
        # This is an extended command
        return ExtendedProtocol(command)
    
if __name__ == "__main__":
    # Example usage
    cmd = create_command("GetBatteryLevel")
    print(cmd)
    print(cmd.generate_request())

    cmd = create_command("GetMessage")
    print(hexlify(cmd.generate_request(messageId=123)))
    
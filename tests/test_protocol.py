"""Many of the tests have been created by hand with a hex editor,
thus there is no better "source code" than the tests themselves :)"""

import unittest
from unittest.mock import patch
from protocol.base import create_command

# Load the actual protocol from the file
# with open("protocol.json", "r") as f:
#    TEST_PROTOCOL = json.load(f)
# TEST_PROTOCOL = protocol


class TestRequests(unittest.TestCase):
    def setUp(self):
        self.get_state_cmd = create_command("GetState")
        self.set_mode_cmd = create_command("SetMode")
        self.get_battery_cmd = create_command("GetBatteryLevel")
        self.get_message_cmd = create_command("GetMessage")
        self.get_task_cmd = create_command("GetTask")
        self.enter_pin_cmd = create_command("EnterOperatorPin")
        self.simple_get_battery = create_command("GetBatteryData")
        # self.simple_send_pin = create_command("SendPin")

    def test_init(self):
        # Test initialization
        self.assertEqual(self.get_state_cmd.major, 4586)
        self.assertEqual(self.get_state_cmd.minor, 2)
        self.assertIsNone(self.get_state_cmd.request_data_type)
        self.assertEqual(self.get_state_cmd.response_data_type, {"response": "uint8"})

        # Test with requestType
        self.assertEqual(self.set_mode_cmd.request_data_type, {"mode": "uint8"})

        # Test with responseType as dict
        self.assertEqual(
            self.get_message_cmd.response_data_type,
            {"time": "tUnixTime", "code": "uint32", "severity": "uint8"},
        )

    def test_generate_request_no_params(self):
        # Test request generation with no parameters
        with patch("random.randint", return_value=42):
            request = self.get_state_cmd.generate_request()
        # Check basic structure: STX, extended protocol, transaction ID, etc.
        self.assertEqual(request[0], 0x02)  # STX
        self.assertEqual(request[1], 0x81)  # Extended protocol
        self.assertEqual(request[4], 42)  # Mocked random transaction ID
        self.assertIsNotNone(self.get_state_cmd.transaction_id)
        self.assertEqual(self.get_state_cmd.transaction_id, 42)
        # Check major command encoding for GetState (4586)
        self.assertEqual(request[5], 0x80 | ((4586 >> 8) & 0xFF))
        self.assertEqual(request[6], 4586 & 0xFF)
        self.assertEqual(request[-1], 0x03)  # ETX

    def test_generate_request_with_params(self):
        # Test request with uint8 parameter
        with patch("random.randint", return_value=42):
            request = self.set_mode_cmd.generate_request(mode=0)
        self.assertEqual(len(request), 12)  # 11 base bytes + 1 param byte
        self.assertEqual(request[0], 0x02)   # STX
        self.assertEqual(request[1], 0x81)   # Extended marker
        self.assertIn(request[4], range(1, 256))  # Valid transaction ID
        self.assertEqual(request[8], 0)      # minor (subcommand for SetMode)
        self.assertEqual(request[9], 0)      # mode value
        self.assertEqual(request[-1], 0x03)  # ETX

        # Test request with uint16 parameter
        with patch("random.randint", return_value=42):
            request = self.enter_pin_cmd.generate_request(code=1234)
        self.assertEqual(len(request), 13)  # 11 base bytes + 2 param bytes
        self.assertIn(request[4], range(1, 256))  # Valid transaction ID
        self.assertEqual(int.from_bytes(request[9:11], byteorder="little"), 1234)

        # Test request with uint32 parameter
        with patch("random.randint", return_value=42):
            request = self.get_message_cmd.generate_request(messageId=1)
        self.assertEqual(len(request), 15)  # 11 base bytes + 4 param bytes
        self.assertIn(request[4], range(1, 256))  # Valid transaction ID
        self.assertEqual(int.from_bytes(request[9:13], byteorder="little"), 1)

    def test_simple_protocol(self):
        # Test simple protocol with no parameters
        request = self.simple_get_battery.generate_request()
        self.assertEqual(request, bytearray.fromhex("021401014e03"))

        ## Test simple protocol with parameter
        # request = self.simple_send_pin.generate_request(one=1, pin=1234)
        # self.assertEqual(request, bytearray.fromhex("020c040001d2046103"))

    #
    ## Test that kwargs order does not matter
    # request = self.simple_send_pin.generate_request(pin=1234, one=1)
    # self.assertEqual(request, bytearray.fromhex("020c040001d2046103"))

    def test_generate_request_missing_params(self):
        # Test error on missing parameters
        with self.assertRaises(ValueError):
            self.set_mode_cmd.generate_request()

        with self.assertRaises(ValueError):
            self.get_message_cmd.generate_request()

        # with self.assertRaises(ValueError):
        #    self.simple_send_pin.generate_request()


class TestResponse(unittest.TestCase):
    # @patch("protocol.protocol", TEST_PROTOCOL)
    def test_parse_response_operatorloggedin(self):
        command = create_command("IsOperatorLoggedIn")
        response = command.parse_response(bytearray.fromhex("02810800039239020000da03"))
        self.assertEqual(response["data"], {"response": 0})

    def test_invalid_responses(self):
        # Invalid responses, valid was 02810800039239020000da03
        command = create_command("IsOperatorLoggedIn")

        # Test invalid STX
        with self.assertRaises(ValueError):
            command.parse_response(bytearray.fromhex("01810800039239020000da03"))

        # Test invalid ETX
        with self.assertRaises(ValueError):
            command.parse_response(bytearray.fromhex("02810800039239020000da04"))


        # Test invalid extended protocol marker
        with self.assertRaises(ValueError):
            command.parse_response(bytearray.fromhex("02820800039239020000da03"))

        # Test invalid length
        with self.assertRaises(ValueError):
            command.parse_response(bytearray.fromhex("02810900039239020000da03"))

        # Test invalid major command
        with self.assertRaises(ValueError):
            command.parse_response(bytearray.fromhex("02810800038239020000da03"))

        # Test invalid CRC
        with self.assertRaises(ValueError):
            command.parse_response(bytearray.fromhex("028108000392390200001103"))

    def test_simple_protocol_responses(self):
        command = create_command("GetSensorData")
        response = command.parse_response(
            bytearray.fromhex("02150c0000005000eaffef03001101cc03")
        )
        self.assertEqual(response["data"]["pitch"], 80)

        command = create_command("GetBatteryData")
        response = command.parse_response(
            bytearray.fromhex("02151500a24bbb04bcfbdc0040060000000000000cfe00006203")
        )
        self.assertEqual(response["data"]["batavoltage"], 19362)



if __name__ == "__main__":
    unittest.main()

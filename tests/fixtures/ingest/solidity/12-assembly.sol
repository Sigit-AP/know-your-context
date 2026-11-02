// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract LowLevel {
    function addAssembly(uint256 a, uint256 b) external pure returns (uint256 result) {
        assembly {
            result := add(a, b)
        }
    }

    function selfBalance() external view returns (uint256) {
        return address(this).balance;
    }
}
